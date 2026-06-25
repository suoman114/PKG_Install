"""
orchestrator.py — 파이프라인 실행 엔진 + 실시간 로그 이벤트 버스

실행 모드(DASHBOARD_MODE 환경변수):
  mock  : 노드 없이 가짜 진행 로그 스트리밍 (기본, 대시보드 개발용)
  check : ansible-playbook --check (dry-run, 실제 변경 없음)
  real  : ansible-playbook 실제 실행

토큰/메모리 규칙: 로그 전문은 SQLite/스트림으로 흘리고 메모리에 무한 적재하지 않는다.
ansible 결과 요약(changed/ok/failed)은 recap 라인을 파싱해 step_status에 기록한다.
"""
import asyncio
import os
import re
import time

from . import pipeline, state

ANSIBLE_DIR = os.environ.get(
    "ANSIBLE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "ansible"),
)
INVENTORY = os.environ.get(
    "ANSIBLE_INVENTORY", os.path.join(ANSIBLE_DIR, "inventory", "hosts.ini")
)
MODE = os.environ.get("DASHBOARD_MODE", "mock").lower()

# ansible PLAY RECAP 라인 파서:  ok=N changed=N failed=N
_RECAP_RE = re.compile(r"ok=(\d+).*?changed=(\d+).*?(?:unreachable=\d+\s+)?failed=(\d+)")


class EventBus(object):
    """SSE 구독자에게 진행 로그/상태 변화를 브로드캐스트."""

    def __init__(self):
        self._subscribers = []

    def subscribe(self):
        q = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        if q in self._subscribers:
            self._subscribers.remove(q)

    def publish(self, event):
        for q in list(self._subscribers):
            q.put_nowait(event)


bus = EventBus()


class Runner(object):
    """한 번에 하나의 파이프라인 실행만 허용한다."""

    def __init__(self):
        self.task = None
        self._cancel = False

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def stop(self):
        self._cancel = True

    def start(self, loop, step_ids, scope, target):
        if self.running:
            return False
        self._cancel = False
        self.task = loop.create_task(self._run_sequence(step_ids, scope, target))
        return True

    async def _run_sequence(self, step_ids, scope, target):
        run_id = state.start_run(scope, target, MODE)
        final = "success"
        self._emit(None, "▶ 실행 시작 (mode={}, scope={}, target={})".format(MODE, scope, target), "head")
        try:
            for sid in step_ids:
                if self._cancel:
                    final = "stopped"
                    self._emit(None, "■ 사용자 중지", "warn")
                    break
                ok = await self._run_step(pipeline.get_step(sid), target)
                if not ok:
                    final = "failed"
                    self._emit(None, "✖ {} 실패 → 이후 단계 보류".format(sid), "error")
                    break
        finally:
            state.end_run(run_id, final)
            self._emit(None, "● 실행 종료: {}".format(final), "head")
            bus.publish({"type": "done", "status": final})
        return final

    async def _run_step(self, step, target):
        if step is None:
            return False
        sid = step.id
        state.set_status(sid, "running", started=time.time())
        bus.publish({"type": "status", "step": sid, "status": "running"})
        self._emit(sid, "── [{}] {} ({}) ──".format(sid, step.name, step.playbook), "head")

        if MODE == "mock":
            ok, changed, okc, failed = await self._run_mock(step)
        else:
            ok, changed, okc, failed = await self._run_ansible(step, target)

        status = "success" if ok else "failed"
        state.set_status(sid, status, changed=changed, ok=okc, failed=failed,
                         ended=time.time())
        bus.publish({"type": "status", "step": sid, "status": status,
                     "changed": changed, "ok": okc, "failed": failed})
        self._emit(sid, "→ {} : ok={} changed={} failed={}".format(status, okc, changed, failed),
                   "head" if ok else "error")
        return ok

    # --- mock: 노드 없이 가짜 진행 로그 ---
    async def _run_mock(self, step):
        lines = [
            "PLAY [vcs] " + "*" * 30,
            "TASK [Gathering Facts] " + "*" * 20,
            "ok: [vcs-node1]",
            "TASK [{}] ".format(step.name) + "*" * 12,
            "changed: [vcs-node1]",
        ]
        for ln in lines:
            if self._cancel:
                break
            self._emit(step.id, ln)
            await asyncio.sleep(0.25)
        changed = 0 if step.idempotent else 1
        self._emit(step.id, "PLAY RECAP " + "*" * 28)
        self._emit(step.id, "vcs-node1 : ok=3 changed={} unreachable=0 failed=0".format(changed))
        if step.verify_cmd:
            self._emit(step.id, "[verify] $ {}".format(step.verify_cmd), "verify")
            self._emit(step.id, "[verify] (mock) OK", "verify")
            state.set_status(step.id, "running", verify="mock-ok")
        return True, changed, 3, 0

    # --- check/real: 실제 ansible-playbook ---
    async def _run_ansible(self, step, target):
        playbook_path = os.path.join(ANSIBLE_DIR, "playbooks", step.playbook)
        cmd = ["ansible-playbook", "-i", INVENTORY, playbook_path]
        if MODE == "check":
            cmd.append("--check")
        if target and target != "all":
            cmd += ["--limit", target]

        self._emit(step.id, "$ " + " ".join(cmd), "verify")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=ANSIBLE_DIR,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError:
            self._emit(step.id, "ansible-playbook 미설치 — mock 모드로 실행하세요.", "error")
            return False, 0, 0, 1

        changed = okc = failed = 0
        while True:
            if self._cancel:
                proc.terminate()
                break
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip("\n")
            self._emit(step.id, line)
            m = _RECAP_RE.search(line)
            if m:
                okc, changed, failed = int(m.group(1)), int(m.group(2)), int(m.group(3))
        rc = await proc.wait()
        ok = (rc == 0 and failed == 0)
        return ok, changed, okc, failed

    def _emit(self, step_id, line, level="info"):
        state.append_log(step_id, line, level)
        bus.publish({"type": "log", "step": step_id, "level": level, "line": line})


runner = Runner()
