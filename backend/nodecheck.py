"""
nodecheck.py — 노드 연결 확인 (SSH 도달성)

대시보드에서 인벤토리에 설정된 노드가 실제로 연결되는지 확인한다.
- 1차: TCP 22 포트 도달성(socket) — 인증·ansible 불필요, 빠름. 네트워크/방화벽 확인.
- 2차: `ansible -m ping`(real/check 모드, ansible 설치 시) — SSH 인증 + 원격 파이썬 확인.
- mock 모드: 가짜 성공으로 흐름만 보여줌.

결과는 이벤트 버스(step="ping")로 스트리밍되어 대시보드 로그에 표시된다.
"""
import socket
import subprocess
import threading

from . import inventory
from .events import emit, emit_status, emit_done
from .orchestrator import ANSIBLE_DIR, INVENTORY, MODE

PING_STEP = "ping"
SSH_PORT = 22
TCP_TIMEOUT = 3.0


class NodeChecker(object):
    def __init__(self):
        self._thread = None
        self.last_status = "idle"

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, target="all"):
        if self.running:
            return False, "이미 연결 확인 중입니다."
        self._thread = threading.Thread(target=self._run, args=(target,), daemon=True)
        self._thread.start()
        return True, "노드 연결 확인 시작"

    def _targets(self, target):
        nodes = inventory.read_inventory().get("nodes", [])
        if target and target != "all":
            nodes = [n for n in nodes if n.get("name") == target]
        return nodes

    def _run(self, target):
        self.last_status = "running"
        emit_status(PING_STEP, "running")
        emit(PING_STEP, "▶ 노드 연결 확인 (target={})".format(target), "head")
        emit(PING_STEP, "TCP {} 포트 도달성을 실제로 확인합니다 (mock 모드와 무관 · SSH 인증은 2차).".format(SSH_PORT), "verify")
        nodes = self._targets(target)
        ok_all = True
        if not nodes:
            emit(PING_STEP, "확인할 노드가 없습니다(인벤토리 비어있음).", "warn")
            ok_all = False
        for n in nodes:
            name = n.get("name")
            host = (n.get("ansible_host") or "").strip()
            if not host:
                emit(PING_STEP, "✗ {} : ansible_host 미설정".format(name), "error")
                ok_all = False
                continue
            # TCP 도달성은 모드와 무관하게 항상 실제로 확인한다.
            reachable, detail = self._tcp(host, SSH_PORT)
            if reachable:
                emit(PING_STEP, "✓ {} ({}:{}) TCP 연결 가능".format(name, host, SSH_PORT), "verify")
            else:
                emit(PING_STEP, "✗ {} ({}:{}) 연결 불가: {}".format(name, host, SSH_PORT, detail), "error")
                ok_all = False

        # 2차: ansible ping (SSH 인증 + 원격 파이썬). real/check 에서만(ansible 필요).
        if MODE != "mock" and nodes:
            ok_all = self._ansible_ping(target) and ok_all
        elif nodes:
            emit(PING_STEP, "참고: SSH 인증·원격 파이썬 확인은 real/check 모드에서 'ansible -m ping'으로 수행됩니다.", "verify")

        self.last_status = "success" if ok_all else "failed"
        emit(PING_STEP, "● 연결 확인 종료: {}".format(self.last_status),
             "head" if ok_all else "error")
        emit_status(PING_STEP, self.last_status)
        emit_done(self.last_status)

    def _tcp(self, host, port):
        try:
            s = socket.create_connection((host, port), timeout=TCP_TIMEOUT)
            s.close()
            return True, ""
        except Exception as e:  # noqa: BLE001 — 사용자에게 사유 노출
            return False, e.__class__.__name__ + (": " + str(e) if str(e) else "")

    def _ansible_ping(self, target):
        pattern = target if (target and target != "all") else "vcs"
        cmd = ["ansible", "-i", INVENTORY, pattern, "-m", "ping", "-o"]
        emit(PING_STEP, "$ " + " ".join(cmd), "verify")
        try:
            proc = subprocess.Popen(
                cmd, cwd=ANSIBLE_DIR, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, bufsize=1, universal_newlines=True,
            )
        except FileNotFoundError:
            emit(PING_STEP, "ansible 미설치 — SSH 인증/원격 파이썬 확인은 건너뜀(TCP 도달성만).", "warn")
            return True  # TCP 결과만으로 판단
        for line in iter(proc.stdout.readline, ""):
            emit(PING_STEP, line.rstrip("\n"))
        proc.stdout.close()
        return proc.wait() == 0


checker = NodeChecker()
