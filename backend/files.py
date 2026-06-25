"""
files.py — ansible 디렉토리 내 YAML/INI 파일 직접 편집 (대시보드 고급 편집기)

구조화 인벤토리 폼으로 다룰 수 없는 항목(커스텀 host_vars, 플레이북, ansible.cfg 등)을
대시보드에서 직접 편집한다. 안전장치:
  - 경로 탈출 방지: ANSIBLE_DIR 하위만 허용
  - 확장자 화이트리스트(.yml/.yaml/.ini/.cfg)만 열람/저장
  - 시크릿 파일(group_vars/vcs.yml)은 제외(값 노출 방지 — 🔒 시크릿 패널로만 관리)
  - 저장 시 YAML 유효성 검사(PyYAML 있을 때). 미설치 시 경고만 하고 저장 허용.
"""
import os

from . import events
from .orchestrator import ANSIBLE_DIR

ROOT = os.path.realpath(ANSIBLE_DIR)
ALLOWED_EXT = (".yml", ".yaml", ".ini", ".cfg")
# 편집 금지(시크릿 노출 방지). 실제 vcs.yml 만 제외하고 .example 은 허용.
DENY_RELPATHS = {os.path.join("inventory", "group_vars", "vcs.yml")}
SKIP_DIRS = {".git", "__pycache__"}


def _safe_abspath(relpath):
    if not relpath or os.path.isabs(relpath) or ".." in relpath.split("/"):
        raise ValueError("잘못된 경로입니다.")
    p = os.path.realpath(os.path.join(ROOT, relpath))
    if p != ROOT and not p.startswith(ROOT + os.sep):
        raise ValueError("허용 범위(ansible/) 밖의 경로입니다.")
    if os.path.splitext(p)[1].lower() not in ALLOWED_EXT:
        raise ValueError("편집 가능한 확장자가 아닙니다(.yml/.yaml/.ini/.cfg).")
    if os.path.normpath(relpath) in DENY_RELPATHS:
        raise ValueError("시크릿 파일은 🔒 시크릿 패널에서만 관리합니다.")
    return p


def _category(relpath):
    top = relpath.split("/", 1)[0]
    if relpath.startswith("playbooks/"):
        return "playbook"
    if relpath.startswith("inventory/"):
        return "inventory"
    return top or "기타"


def list_files():
    out = []
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in names:
            if os.path.splitext(n)[1].lower() not in ALLOWED_EXT:
                continue
            rel = os.path.relpath(os.path.join(base, n), ROOT).replace(os.sep, "/")
            if os.path.normpath(rel) in DENY_RELPATHS:
                continue
            out.append({"path": rel, "category": _category(rel)})
    out.sort(key=lambda x: (x["category"], x["path"]))
    return out


def read_file(relpath):
    p = _safe_abspath(relpath)
    if not os.path.isfile(p):
        raise ValueError("파일이 존재하지 않습니다.")
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def _validate_yaml(relpath, content):
    """YAML이면 파싱 검사. (ok, message). PyYAML 미설치 시 (True, 경고)."""
    if os.path.splitext(relpath)[1].lower() not in (".yml", ".yaml"):
        return True, ""
    try:
        import yaml
    except ImportError:
        return True, "(PyYAML 미설치 — 문법 검사 생략)"
    try:
        list(yaml.safe_load_all(content))
        return True, ""
    except Exception as e:  # noqa: BLE001
        first = str(e).splitlines()[0] if str(e) else e.__class__.__name__
        return False, "YAML 문법 오류: {}".format(first)


def write_file(relpath, content):
    p = _safe_abspath(relpath)
    if not os.path.isfile(p):
        raise ValueError("새 파일 생성은 지원하지 않습니다(기존 파일만 편집).")
    ok, msg = _validate_yaml(relpath, content)
    if not ok:
        return False, msg
    # 개행 정규화(CRLF→LF), 끝 개행 보장
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    events.emit("file", "파일 저장: ansible/{}{}".format(relpath, (" " + msg) if msg else ""), "verify")
    return True, "저장됨" + ((" " + msg) if msg else "")
