"""
app.py — FastAPI 엔트리 (대시보드 백엔드)

라우트:
  GET  /                 대시보드 정적 페이지
  GET  /api/pipeline     step 정의 + phase 목록
  GET  /api/status       step별 현재 상태
  GET  /api/inventory    hosts.ini / host_vars 내용
  POST /api/run          실행 시작 {scope: all|phase|step, id?, target?}
  POST /api/stop         실행 중지
  POST /api/reset        상태/로그 초기화
  GET  /api/logs/stream  SSE 실시간 로그
  GET  /api/logs         로그 폴링(after_id)
  GET  /api/report       간이 결과 요약(JSON)  ※ 정식 보고서는 report-agent가 확장
"""
import asyncio
import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import (FileResponse, JSONResponse, StreamingResponse)

from . import orchestrator, pipeline, state

BASE = os.path.dirname(os.path.dirname(__file__))
FRONTEND = os.path.join(BASE, "frontend")
ANSIBLE_DIR = orchestrator.ANSIBLE_DIR

app = FastAPI(title="LTE-R VCS PKG Install Dashboard")


@app.on_event("startup")
def _startup():
    state.init_db()
    state.seed_steps(pipeline.all_step_ids())


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND, "index.html"))


@app.get("/api/pipeline")
def get_pipeline():
    return {
        "mode": orchestrator.MODE,
        "phases": [{"key": k, "label": v} for k, v in pipeline.PHASES],
        "steps": pipeline.as_dicts(),
    }


@app.get("/api/status")
def get_status():
    return {"steps": state.get_all_status()}


@app.get("/api/inventory")
def get_inventory():
    out = {"hosts": None, "host_vars": {}}
    inv = os.path.join(ANSIBLE_DIR, "inventory", "hosts.ini")
    if os.path.isfile(inv):
        with open(inv, "r", encoding="utf-8") as f:
            out["hosts"] = f.read()
    hv_dir = os.path.join(ANSIBLE_DIR, "inventory", "host_vars")
    if os.path.isdir(hv_dir):
        for name in sorted(os.listdir(hv_dir)):
            with open(os.path.join(hv_dir, name), "r", encoding="utf-8") as f:
                out["host_vars"][name] = f.read()
    return out


def _resolve_step_ids(scope, step_id):
    if scope == "all":
        return pipeline.all_step_ids()
    if scope == "phase":
        return [s.id for s in pipeline.steps_for_phase(step_id)]
    if scope == "step":
        return [step_id] if pipeline.get_step(step_id) else []
    return []


@app.post("/api/run")
async def run(request: Request):
    body = await request.json()
    scope = body.get("scope", "all")
    step_id = body.get("id")
    target = body.get("target", "all")
    if orchestrator.runner.running:
        return JSONResponse({"error": "이미 실행 중입니다."}, status_code=409)
    step_ids = _resolve_step_ids(scope, step_id)
    if not step_ids:
        return JSONResponse({"error": "실행할 step이 없습니다."}, status_code=400)
    loop = asyncio.get_event_loop()
    orchestrator.runner.start(loop, step_ids, scope, "{}".format(target))
    return {"started": step_ids, "scope": scope, "target": target}


@app.post("/api/stop")
def stop():
    orchestrator.runner.stop()
    return {"stopping": True}


@app.post("/api/reset")
def reset():
    if orchestrator.runner.running:
        return JSONResponse({"error": "실행 중에는 초기화할 수 없습니다."}, status_code=409)
    state.reset_all(pipeline.all_step_ids())
    return {"reset": True}


@app.get("/api/logs")
def logs(after_id: int = 0):
    return {"logs": state.get_logs(after_id)}


@app.get("/api/logs/stream")
async def logs_stream(request: Request):
    q = orchestrator.bus.subscribe()

    async def gen():
        try:
            # 연결 직후 핑 1회
            yield "event: ping\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield "data: {}\n\n".format(json.dumps(ev, ensure_ascii=False))
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            orchestrator.bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/report")
def report():
    """간이 집계(JSON). 정식 HTML/MD 보고서는 report-agent(backend/report.py)가 확장."""
    statuses = state.get_all_status()
    summary = {"success": 0, "failed": 0, "running": 0, "pending": 0, "skipped": 0}
    for s in statuses.values():
        summary[s["status"]] = summary.get(s["status"], 0) + 1
    by_phase = {}
    for st in pipeline.STEPS:
        ph = st.phase
        cur = statuses.get(st.id, {})
        by_phase.setdefault(ph, []).append({
            "id": st.id, "name": st.name, "playbook": st.playbook,
            "status": cur.get("status", "pending"),
            "changed": cur.get("changed", 0), "ok": cur.get("ok", 0),
            "failed": cur.get("failed", 0), "verify": cur.get("verify"),
        })
    return {"mode": orchestrator.MODE, "summary": summary, "by_phase": by_phase}
