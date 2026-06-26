"""
app.py — Flask 엔트리 (대시보드 백엔드)

CentOS7 시스템 Python 3.6 호환을 위해 Flask(동기) + 스레드 기반 실행 엔진 사용.

라우트:
  GET  /                  대시보드 정적 페이지
  GET  /api/pipeline      step 정의 + phase 목록 + mode
  GET  /api/status        step별 현재 상태
  GET  /api/inventory     hosts.ini / host_vars 내용
  POST /api/run           실행 시작 {scope: all|phase|step, id?, target?}
  POST /api/stop          실행 중지
  POST /api/reset         상태/로그 초기화
  GET  /api/logs          로그 폴링(after_id)
  GET  /api/logs/stream   SSE 실시간 로그
  GET  /api/report        간이 결과 요약(JSON)
  GET  /api/git           git 자산 설정/상태
  POST /api/git/config    git 설정 저장 {git_url, git_branch, asset_dest}
  POST /api/git/sync      clone/pull 실행
"""
import json
import os
import queue

from flask import Flask, Response, jsonify, request, send_from_directory

from . import (assetcheck, files, gitassets, inventory, nodecheck, orchestrator,
               pipeline, report, secrets, state)
from .events import bus, emit

BASE = os.path.dirname(os.path.dirname(__file__))
FRONTEND = os.path.join(BASE, "frontend")
ANSIBLE_DIR = orchestrator.ANSIBLE_DIR

app = Flask(__name__)

state.init_db()
state.seed_steps(pipeline.all_step_ids())


@app.route("/")
def index():
    # 대시보드는 자주 갱신되므로 캐시 금지 → git pull 후 새로고침만 해도 최신 UI 반영
    resp = send_from_directory(FRONTEND, "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/api/pipeline")
def get_pipeline():
    return jsonify({
        "mode": orchestrator.MODE,
        "phases": [{"key": k, "label": v} for k, v in pipeline.PHASES],
        "steps": pipeline.as_dicts(),
    })


@app.route("/api/status")
def get_status():
    return jsonify({"steps": state.get_all_status()})


@app.route("/api/inventory")
def get_inventory():
    return jsonify(inventory.read_inventory())


@app.route("/api/inventory", methods=["POST"])
def save_inventory():
    if orchestrator.runner.running:
        return jsonify({"error": "실행 중에는 인벤토리를 저장할 수 없습니다."}), 409
    body = request.get_json(force=True, silent=True) or {}
    errors = inventory.write_inventory(body)
    if errors:
        return jsonify({"error": "검증 실패", "errors": errors}), 400
    return jsonify(inventory.read_inventory())


def _resolve_step_ids(scope, step_id):
    if scope == "all":
        return pipeline.all_step_ids()
    if scope == "phase":
        return [s.id for s in pipeline.steps_for_phase(step_id)]
    if scope == "step":
        return [step_id] if pipeline.get_step(step_id) else []
    return []


@app.route("/api/run", methods=["POST"])
def run():
    body = request.get_json(force=True, silent=True) or {}
    scope = body.get("scope", "all")
    step_id = body.get("id")
    target = body.get("target", "all")
    idempotency = bool(body.get("idempotency"))
    if orchestrator.runner.running:
        return jsonify({"error": "이미 파이프라인이 실행 중입니다."}), 409
    step_ids = _resolve_step_ids(scope, step_id)
    if not step_ids:
        return jsonify({"error": "실행할 step이 없습니다."}), 400

    # 사전 점검: 실제 실행(real/check)에서 필요한 시크릿이 설정됐는지 확인
    if orchestrator.MODE != "mock":
        need = []
        for sid in step_ids:
            st = pipeline.get_step(sid)
            for s in (st.required_secrets if st else []):
                if s not in need:
                    need.append(s)
        if need:
            status = secrets.status()
            if not status.get("vault"):
                labels = {k["key"]: k["label"] for k in status["keys"]}
                isset = {k["key"]: k["set"] for k in status["keys"]}
                missing = [s for s in need if not isset.get(s)]
                if missing:
                    return jsonify({
                        "error": "필수 시크릿 미설정: {} — ⚙ 설정 → 🔒 시크릿 에서 입력 후 저장하세요.".format(
                            ", ".join("{}({})".format(labels.get(m, m), m) for m in missing)),
                        "missing_secrets": missing,
                    }), 400

        # 사전 점검: 필요한 자산(RPM/파일)이 asset_root 에 있는지 확인
        ac = assetcheck.check(step_ids)
        if not ac["root_exists"]:
            return jsonify({"error": "자산 경로(asset_root)가 없습니다: {} — ⚙ 설정 → 인벤토리 asset_root 확인/자산 동기화.".format(
                ac["asset_root"]), "missing_assets": ac["missing"]}), 400
        if ac["missing"]:
            return jsonify({
                "error": "자산 누락({}): {} — 자산 동기화 또는 디렉토리에 파일을 두세요.".format(
                    ac["asset_root"], ", ".join(ac["missing"])),
                "missing_assets": ac["missing"],
            }), 400

    if gitassets.syncer.running:
        emit(None, "⚠ 자산 동기화가 진행 중입니다 — 자산이 아직 불완전할 수 있으니 주의하세요.", "warn")
    orchestrator.runner.start(step_ids, scope, "{}".format(target), idempotency=idempotency)
    return jsonify({"started": step_ids, "scope": scope, "target": target,
                    "idempotency": idempotency})


@app.route("/api/assets/check")
def assets_check():
    return jsonify(assetcheck.check())


@app.route("/api/nodes/check", methods=["POST"])
def nodes_check():
    if orchestrator.runner.running:
        return jsonify({"error": "파이프라인 실행 중에는 연결 확인을 할 수 없습니다."}), 409
    body = request.get_json(force=True, silent=True) or {}
    ok, msg = nodecheck.checker.start(body.get("target", "all"))
    if not ok:
        return jsonify({"error": msg}), 409
    return jsonify({"checking": True, "message": msg})


@app.route("/api/stop", methods=["POST"])
def stop():
    orchestrator.runner.stop()
    return jsonify({"stopping": True})


@app.route("/api/reset", methods=["POST"])
def reset():
    if orchestrator.runner.running:
        return jsonify({"error": "실행 중에는 초기화할 수 없습니다."}), 409
    state.reset_all(pipeline.all_step_ids())
    return jsonify({"reset": True})


@app.route("/api/logs")
def logs():
    after_id = request.args.get("after_id", 0, type=int)
    return jsonify({"logs": state.get_logs(after_id)})


@app.route("/api/logs/stream")
def logs_stream():
    q = bus.subscribe()

    def gen():
        try:
            yield "event: ping\ndata: {}\n\n"
            while True:
                try:
                    ev = q.get(timeout=15)
                    yield "data: {}\n\n".format(json.dumps(ev, ensure_ascii=False))
                except queue.Empty:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            bus.unsubscribe(q)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return Response(gen(), mimetype="text/event-stream", headers=headers)


@app.route("/api/report")
def get_report():
    return jsonify(report.collect())


@app.route("/api/report.md")
def get_report_md():
    body = report.to_markdown()
    return Response(body, mimetype="text/markdown", headers={
        "Content-Disposition": 'attachment; filename="vcs_install_report.md"'})


@app.route("/api/report.html")
def get_report_html():
    dl = request.args.get("download")
    headers = {}
    if dl:
        headers["Content-Disposition"] = 'attachment; filename="vcs_install_report.html"'
    return Response(report.to_html(), mimetype="text/html", headers=headers)


# ---- Git 자산 동기화 ----
@app.route("/api/git")
def git_get():
    cfg = gitassets.get_config()
    dest = cfg["asset_dest"]
    cfg["exists"] = os.path.isdir(os.path.join(dest, ".git"))
    cfg["status"] = gitassets.syncer.last_status
    cfg["running"] = gitassets.syncer.running
    return jsonify(cfg)


@app.route("/api/git/config", methods=["POST"])
def git_config():
    body = request.get_json(force=True, silent=True) or {}
    cfg = gitassets.set_config(
        git_url=body.get("git_url"),
        git_branch=body.get("git_branch"),
        asset_dest=body.get("asset_dest"),
        git_user=body.get("git_user"),
        git_token=body.get("git_token"),
    )
    return jsonify(cfg)


@app.route("/api/files")
def files_list():
    return jsonify({"files": files.list_files()})


@app.route("/api/files/content")
def files_read():
    path = request.args.get("path", "")
    try:
        return jsonify({"path": path, "content": files.read_file(path)})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/files/content", methods=["POST"])
def files_write():
    if orchestrator.runner.running:
        return jsonify({"error": "실행 중에는 파일을 저장할 수 없습니다."}), 409
    body = request.get_json(force=True, silent=True) or {}
    path = body.get("path", "")
    try:
        ok, msg = files.write_file(path, body.get("content", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"path": path, "message": msg})


@app.route("/api/secrets")
def secrets_get():
    return jsonify(secrets.status())


@app.route("/api/secrets", methods=["POST"])
def secrets_set():
    body = request.get_json(force=True, silent=True) or {}
    ok, msg = secrets.write(body)
    if not ok:
        return jsonify({"error": msg}), 400
    out = secrets.status()
    out["message"] = msg
    return jsonify(out)


@app.route("/api/git/stop", methods=["POST"])
def git_stop():
    ok, msg = gitassets.syncer.stop()
    return jsonify({"stopping": ok, "message": msg})


@app.route("/api/git/sync", methods=["POST"])
def git_sync():
    if orchestrator.runner.running:
        return jsonify({"error": "파이프라인 실행 중에는 동기화할 수 없습니다."}), 409
    ok, msg = gitassets.syncer.start()
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"syncing": True, "message": msg})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8800"))
    # threaded=True: SSE 스트림과 실행 스레드 동시 처리
    app.run(host="0.0.0.0", port=port, threaded=True)
