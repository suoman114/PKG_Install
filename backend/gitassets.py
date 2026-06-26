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
import shutil
import signal
import subprocess
import threading
import time

try:  # py3
    from urllib.parse import quote, urlsplit, urlunsplit
except ImportError:  # pragma: no cover
    from urllib import quote
    from urlparse import urlsplit, urlunsplit

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


def _authed_url(url, user, token):
    """http(s) URL 에 user:token 자격증명을 삽입. (authed_url, clean_url) 반환.
    non-http(예: SSH git@…)나 토큰 없음이면 원본 그대로."""
    if not token or not url.lower().startswith(("http://", "https://")):
        return url, url
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = "{}:{}".format(host, parts.port)
    userinfo = "{}:{}".format(quote(user or "x-token-auth", safe=""),
                              quote(token, safe=""))
    authed = urlunsplit((parts.scheme, userinfo + "@" + host,
                         parts.path, parts.query, parts.fragment))
    return authed, url


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
            # git clone 은 자식(git-remote-*, index-pack)을 띄우므로 프로세스 그룹 전체 종료
            self._kill_group(p, signal.SIGTERM)
        return True, "중지 요청됨"

    @staticmethod
    def _kill_group(proc, sig):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except Exception:  # noqa: BLE001 — 그룹 없으면 단일 종료로 폴백
            try:
                proc.send_signal(sig)
            except Exception:  # noqa: BLE001
                pass

    def _sync(self, cfg):
        url, branch, dest = cfg["git_url"], cfg["git_branch"], cfg["asset_dest"]
        user = state.get_setting("git_user", "")
        token = state.get_setting("git_token", "")
        authed_url, clean_url = _authed_url(url, user, token)
        # 로그/에러에서 자격증명 가리기: authed_url→clean_url, 토큰→***
        red = [(authed_url, clean_url), (token, "***")] if token else []
        if token:
            emit(GIT_STEP, "인증 사용: user={} token=***".format(user or "(x-token-auth)"), "verify")
        self.last_status = "running"
        emit_status(GIT_STEP, "running")
        started = time.time()
        try:
            git_dir = os.path.join(dest, ".git")
            fresh = not os.path.isdir(git_dir)
            if not fresh:
                emit(GIT_STEP, "기존 저장소 감지 → pull: {}".format(dest), "head")
                # 저장된 remote 에 의존하지 않고 authed_url 직접 사용(자격증명 디스크 미저장)
                cmd = ["git", "-C", dest, "pull", "--ff-only", authed_url, branch]
            else:
                parent = os.path.dirname(dest) or "."
                if not os.path.isdir(parent):
                    os.makedirs(parent, exist_ok=True)
                emit(GIT_STEP, "신규 clone: {} (branch={}) → {}".format(url, branch, dest), "head")
                cmd = ["git", "clone", "--branch", branch, "--depth", "1", authed_url, dest]

            ok = self._stream(cmd, red)
            if ok and fresh and token:
                # clone 시 origin 에 박힌 자격증명을 제거(.git/config 평문 토큰 방지)
                subprocess.call(["git", "-C", dest, "remote", "set-url", "origin", clean_url])
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

    @staticmethod
    def _redact(text, pairs):
        for sensitive, safe in (pairs or []):
            if sensitive:
                text = text.replace(sensitive, safe)
        return text

    def _stream(self, cmd, redact=None):
        emit(GIT_STEP, "$ " + self._redact(" ".join(cmd), redact), "verify")
        # 새 세션(프로세스 그룹)으로 띄워 중지 시 자식까지 한 번에 종료 가능하게
        # GIT_TERMINAL_PROMPT=0: 인증 실패 시 사용자명 프롬프트로 멈추지 않고 즉시 실패
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        popen_kw = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        bufsize=1, universal_newlines=True, env=env)
        if hasattr(os, "setsid"):
            popen_kw["preexec_fn"] = os.setsid
        try:
            proc = subprocess.Popen(cmd, **popen_kw)
        except FileNotFoundError:
            emit(GIT_STEP, "git 미설치 — 'yum install -y git' 후 재시도하세요.", "error")
            return False
        self._proc = proc
        for line in iter(proc.stdout.readline, ""):
            if self._cancel:
                self._kill_group(proc, signal.SIGKILL)  # 즉시 확실히 종료
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
