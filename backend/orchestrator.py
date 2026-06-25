"""
orchestrator.py — 파이프라인 실행 엔진 (스레드 기반, Flask 호환)

실행 모드(DASHBOARD_MODE 환경변수):
  mock  : 노드 없이 가짜 진행 로그 스트리밍 (기본, 대시보드 개발용)
  check : ansible-playbook --check (dry-run)
  real  : ansible-playbook 실제 실행

로그 전문은 SQLite/스트림으로 흘리고 메모리에 무한 적재하지 않는다.
ansible PLAY RECAP 라인을 파싱해 changed/ok/failed 를 step_status에 기록한다.
"""
import os
import re
import subprocess
import threading
import time

from . import pipeline, state
from .events import bus, emit, emit_status, emit_done  # noqa: F401 (bus re-export)

ANSIBLE_DIR = os.environ.get(
    "ANSIBLE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "ansible"),
)
INVENTORY = os.environ.get(
    "ANSIBLE_INVENTORY", os.path.join(ANSIBLE_DIR, "inventory", "hosts.ini")
)
MODE = os.environ.get("DASHBOARD_MODE", "mock").lower()

_RECAP_RE = re.compile(r"ok=(\d+).*?changed=(\d+).*?(?:unreachable=\d+\s+)?failed=(\d+)")


class Runner(object):
    """한 번에 하나의 파이프라인 실행만 허용한다."""

    def __init__(self):
        self._thread = None
        self._cancel = False

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def stop(self):
        self._cancel = True

    def start(self, step_ids, scope, target):
        if self.running:
            return False
        self._cancel = False
        self._thread = threading.Thread(
            target=self._run_sequence, args=(step_ids, scope, target), daemon=True
        )
        self._thread.start()
        return True

    def _run_sequence(self, step_ids, scope, target):
        run_id = state.start_run(scope, target, MODE)
        final = "success"
        emit(None, "▶ 실행 시작 (mode={}, scope={}, target={})".format(MODE, scope, target), "head")
        try:
            for sid in step_ids:
                if self._cancel:
                    final = "stopped"
                    emit(None, "■ 사용자 중지", "warn")
                    break
                if not self._run_step(pipeline.get_step(sid), target):
                    final = "failed"
                    emit(None, "✖ {} 실패 → 이후 단계 보류".format(sid), "error")
                    break
        finally:
            state.end_run(run_id, final)
            emit(None, "● 실행 종료: {}".format(final), "head")
            emit_done(final)

    def _run_step(self, step, target):
        if step is None:
            return False
        sid = step.id
        state.set_status(sid, "running", started=time.time())
        emit_status(sid, "running")
        emit(sid, "── [{}] {} ({}) ──".format(sid, step.name, step.playbook), "head")

        if MODE == "mock":
            ok, changed, okc, failed = self._run_mock(step)
        else:
            ok, changed, okc, failed = self._run_ansible(step, target)

        status = "success" if ok else "failed"
        state.set_status(sid, status, changed=changed, ok=okc, failed=failed, ended=time.time())
        emit_status(sid, status, changed=changed, ok=okc, failed=failed)
        emit(sid, "→ {} : ok={} changed={} failed={}".format(status, okc, changed, failed),
             "head" if ok else "error")
        return ok

    def _run_mock(self, step):
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
            emit(step.id, ln)
            time.sleep(0.25)
        changed = 0 if step.idempotent else 1
        emit(step.id, "PLAY RECAP " + "*" * 28)
        emit(step.id, "vcs-node1 : ok=3 changed={} unreachable=0 failed=0".format(changed))
        if step.verify_cmd:
            emit(step.id, "[verify] $ {}".format(step.verify_cmd), "verify")
            emit(step.id, "[verify] (mock) OK", "verify")
            state.set_status(step.id, "running", verify="mock-ok")
        return True, changed, 3, 0

    def _run_ansible(self, step, target):
        playbook_path = os.path.join(ANSIBLE_DIR, "playbooks", step.playbook)
        cmd = ["ansible-playbook", "-i", INVENTORY, playbook_path]
        if MODE == "check":
            cmd.append("--check")
        if target and target != "all":
            cmd += ["--limit", target]
        emit(step.id, "$ " + " ".join(cmd), "verify")
        try:
            proc = subprocess.Popen(
                cmd, cwd=ANSIBLE_DIR, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, bufsize=1, universal_newlines=True,
            )
        except FileNotFoundError:
            emit(step.id, "ansible-playbook 미설치 — mock 모드로 실행하세요.", "error")
            return False, 0, 0, 1

        changed = okc = failed = 0
        for line in iter(proc.stdout.readline, ""):
            if self._cancel:
                proc.terminate()
                break
            line = line.rstrip("\n")
            emit(step.id, line)
            m = _RECAP_RE.search(line)
            if m:
                okc, changed, failed = int(m.group(1)), int(m.group(2)), int(m.group(3))
        proc.stdout.close()
        rc = proc.wait()
        return (rc == 0 and failed == 0), changed, okc, failed


runner = Runner()
