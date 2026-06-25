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
import base64
import os
import shutil
import subprocess
import threading
import time

from . import state
from .events import bus, emit, emit_status, emit_done

REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
DEFAULT_DEST = os.environ.get("ASSET_DEST", os.path.join(REPO_ROOT, "assets"))
GIT_STEP = "git"  # 로그/상태에서 사용하는 가상 step id


def get_config():
    # 토큰 값은 절대 반환하지 않고 설정 여부(git_auth)만 노출한다.
    return {
        "git_url": state.get_setting("git_url", ""),
        "git_branch": state.get_setting("git_branch", "main"),
        "asset_dest": state.get_setting("asset_dest", DEFAULT_DEST),
        "git_user": state.get_setting("git_user", ""),
        "git_auth": bool(state.get_setting("git_token", "")),
    }


def set_config(git_url=None, git_branch=None, asset_dest=None,
               git_user=None, git_token=None):
    if git_url is not None:
        state.set_setting("git_url", git_url.strip())
    if git_branch is not None:
        state.set_setting("git_branch", (git_branch or "main").strip())
    if asset_dest is not None:
        state.set_setting("asset_dest", (asset_dest or DEFAULT_DEST).strip())
    if git_user is not None:
        state.set_setting("git_user", git_user.strip())
    # 빈 토큰은 기존 값 유지(시크릿과 동일). 지우려면 "-" 한 글자.
    if git_token:
        state.set_setting("git_token", "" if git_token == "-" else git_token)
    return get_config()


def _auth_args(user, token):
    """http(s) 사설 저장소용 Basic 인증 헤더. .git/config 에 저장되지 않음."""
    if not token:
        return [], None
    cred = base64.b64encode(
        "{}:{}".format(user or "x-access-token", token).encode("utf-8")
    ).decode("ascii")
    return ["-c", "http.extraHeader=Authorization: Basic {}".format(cred)], cred


class Syncer(object):
    def __init__(self):
        self._thread = None
        self.last_status = "idle"
        self._cancel = False
        self._proc = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return False, "이미 동기화 중입니다."
        cfg = get_config()
        if not cfg["git_url"]:
            return False, "git_url 이 설정되지 않았습니다."
        self._cancel = False
        self._thread = threading.Thread(target=self._sync, args=(cfg,), daemon=True)
        self._thread.start()
        return True, "동기화 시작"

    def stop(self):
        if not self.running:
            return False, "동기화 중이 아닙니다."
        self._cancel = True
        p = self._proc
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
        return True, "중지 요청됨"

    def _sync(self, cfg):
        url, branch, dest = cfg["git_url"], cfg["git_branch"], cfg["asset_dest"]
        user = state.get_setting("git_user", "")
        token = state.get_setting("git_token", "")
        auth, cred = _auth_args(user, token)
        if token:
            emit(GIT_STEP, "인증 사용: user={} token=***".format(user or "(x-access-token)"), "verify")
        self.last_status = "running"
        emit_status(GIT_STEP, "running")
        started = time.time()
        try:
            git_dir = os.path.join(dest, ".git")
            fresh = not os.path.isdir(git_dir)
            if not fresh:
                emit(GIT_STEP, "기존 저장소 감지 → pull: {}".format(dest), "head")
                cmd = ["git"] + auth + ["-C", dest, "pull", "--ff-only", "origin", branch]
            else:
                parent = os.path.dirname(dest) or "."
                if not os.path.isdir(parent):
                    os.makedirs(parent, exist_ok=True)
                emit(GIT_STEP, "신규 clone: {} (branch={}) → {}".format(url, branch, dest), "head")
                cmd = ["git"] + auth + ["clone", "--branch", branch, "--depth", "1", url, dest]

            ok = self._stream(cmd, redact=cred)
            if self._cancel:
                emit(GIT_STEP, "■ 사용자 중지", "warn")
                if fresh and os.path.isdir(dest):
                    shutil.rmtree(dest, ignore_errors=True)
                    emit(GIT_STEP, "부분 clone 정리됨: {}".format(dest), "warn")
                self.last_status = "stopped"
                emit_status(GIT_STEP, "stopped")
            elif ok:
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
            self._proc = None
            emit_done(self.last_status)

    def _redact(self, text, cred):
        out = text
        if cred:
            out = out.replace("Authorization: Basic {}".format(cred), "Authorization: Basic ***")
            out = out.replace(cred, "***")
        return out

    def _stream(self, cmd, redact=None):
        emit(GIT_STEP, "$ " + self._redact(" ".join(cmd), redact), "verify")
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, universal_newlines=True,
            )
        except FileNotFoundError:
            emit(GIT_STEP, "git 미설치 — 'yum install -y git' 후 재시도하세요.", "error")
            return False
        self._proc = proc
        for line in iter(proc.stdout.readline, ""):
            if self._cancel:
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
                break
            emit(GIT_STEP, self._redact(line.rstrip("\n"), redact))
        proc.stdout.close()
        rc = proc.wait()
        return rc == 0 and not self._cancel

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
