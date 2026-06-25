"""
gitassets.py — Git 자산 동기화 (사내망에서 RPM/파일 선반입)

목적: 폐쇄망 설치 전, 인터넷이 되는 사내망에서 대시보드를 통해
      RPM·설정·패키지 자산이 담긴 git 저장소를 clone/pull 해 로컬에 받아둔다.
      이후 셋업된 서버를 폐쇄망 현장으로 이관해 설치를 진행한다.

설정(state.settings 에 영속):
  git_url    : 자산 저장소 URL
  git_branch : 브랜치 (기본 main)
  asset_dest : clone 대상 경로 (기본 <repo>/assets) → 플레이북 asset_root 와 매핑
"""
import os
import subprocess
import threading
import time

from . import state
from .events import bus, emit, emit_status, emit_done

REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
DEFAULT_DEST = os.environ.get("ASSET_DEST", os.path.join(REPO_ROOT, "assets"))
GIT_STEP = "git"  # 로그/상태에서 사용하는 가상 step id


def get_config():
    return {
        "git_url": state.get_setting("git_url", ""),
        "git_branch": state.get_setting("git_branch", "main"),
        "asset_dest": state.get_setting("asset_dest", DEFAULT_DEST),
    }


def set_config(git_url=None, git_branch=None, asset_dest=None):
    if git_url is not None:
        state.set_setting("git_url", git_url.strip())
    if git_branch is not None:
        state.set_setting("git_branch", (git_branch or "main").strip())
    if asset_dest is not None:
        state.set_setting("asset_dest", (asset_dest or DEFAULT_DEST).strip())
    return get_config()


class Syncer(object):
    def __init__(self):
        self._thread = None
        self.last_status = "idle"

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return False, "이미 동기화 중입니다."
        cfg = get_config()
        if not cfg["git_url"]:
            return False, "git_url 이 설정되지 않았습니다."
        self._thread = threading.Thread(target=self._sync, args=(cfg,), daemon=True)
        self._thread.start()
        return True, "동기화 시작"

    def _sync(self, cfg):
        url, branch, dest = cfg["git_url"], cfg["git_branch"], cfg["asset_dest"]
        self.last_status = "running"
        emit_status(GIT_STEP, "running")
        started = time.time()
        try:
            git_dir = os.path.join(dest, ".git")
            if os.path.isdir(git_dir):
                emit(GIT_STEP, "기존 저장소 감지 → pull: {}".format(dest), "head")
                cmd = ["git", "-C", dest, "pull", "--ff-only", "origin", branch]
            else:
                parent = os.path.dirname(dest) or "."
                if not os.path.isdir(parent):
                    os.makedirs(parent, exist_ok=True)
                emit(GIT_STEP, "신규 clone: {} (branch={}) → {}".format(url, branch, dest), "head")
                cmd = ["git", "clone", "--branch", branch, "--depth", "1", url, dest]

            ok = self._stream(cmd)
            if ok:
                size = self._dir_summary(dest)
                emit(GIT_STEP, "✓ 동기화 완료 ({:.1f}s) — {}".format(time.time() - started, size), "head")
                self.last_status = "success"
                emit_status(GIT_STEP, "success")
            else:
                self.last_status = "failed"
                emit_status(GIT_STEP, "failed")
        except Exception as e:  # noqa: BLE001 — 사용자에게 그대로 노출
            emit(GIT_STEP, "예외: {}".format(e), "error")
            self.last_status = "failed"
            emit_status(GIT_STEP, "failed")
        finally:
            emit_done(self.last_status)

    def _stream(self, cmd):
        emit(GIT_STEP, "$ " + " ".join(cmd), "verify")
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, universal_newlines=True,
            )
        except FileNotFoundError:
            emit(GIT_STEP, "git 미설치 — 'yum install -y git' 후 재시도하세요.", "error")
            return False
        for line in iter(proc.stdout.readline, ""):
            emit(GIT_STEP, line.rstrip("\n"))
        proc.stdout.close()
        return proc.wait() == 0

    def _dir_summary(self, dest):
        files = 0
        rpms = 0
        for _root, _dirs, names in os.walk(dest):
            if ".git" in _root:
                continue
            for n in names:
                files += 1
                if n.endswith(".rpm"):
                    rpms += 1
        return "files={} rpm={}".format(files, rpms)


syncer = Syncer()
