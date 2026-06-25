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

from . import gitassets, inventory, orchestrator, pipeline, report, state
from .events import bus

BASE = os.path.dirname(os.path.dirname(__file__))
FRONTEND = os.path.join(BASE, "frontend")
ANSIBLE_DIR = orchestrator.ANSIBLE_DIR

app = Flask(__name__)

state.init_db()
state.seed_steps(pipeline.all_step_ids())


@app.route("/")
def index():
    return send_from_directory(FRONTEND, "index.html")


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
    if orchestrator.runner.running or gitassets.syncer.running:
        return jsonify({"error": "다른 작업이 실행 중입니다."}), 409
    step_ids = _resolve_step_ids(scope, step_id)
    if not step_ids:
        return jsonify({"error": "실행할 step이 없습니다."}), 400
    orchestrator.runner.start(step_ids, scope, "{}".format(target))
    return jsonify({"started": step_ids, "scope": scope, "target": target})


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
    )
    return jsonify(cfg)


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
