"""
secrets.py — 시크릿(비밀번호) 관리: ansible group_vars/vcs.yml 생성/갱신

평문 비밀번호를 플레이북에서 제거하고 vcs 그룹 변수로 외부화한다. 이 파일은
.gitignore 처리되며(커밋 금지), 대시보드 "시크릿" 패널에서 입력해 저장한다.
강화 시 `ansible-vault encrypt`로 암호화 권장(이 경우 대시보드는 읽기/병합 불가 →
상태만 표시).

보안: API는 값(value)을 절대 반환하지 않고 "설정됨" 여부(bool)만 노출한다.
파일은 0600 권한으로 기록한다.
"""
import os
import re

from .orchestrator import ANSIBLE_DIR

VCS_YML = os.path.join(ANSIBLE_DIR, "inventory", "group_vars", "vcs.yml")

# (key, 라벨) — 플레이북 참조 위치
SECRET_KEYS = [
    ("ssh_password", "SSH 접속 (0)"),
    ("mariadb_root_password", "MariaDB root (8-1)"),
    ("mariadb_vcsm_password", "MariaDB vcsm (8-1)"),
    ("replication_password", "복제 (8-3)"),
    ("rmq_vcm_password", "RabbitMQ vcm (6)"),
    ("vcs_os_password", "OS 계정 vcs (9-1)"),
    ("vcweb_os_password", "OS 계정 vcweb (10-1)"),
]
_KEYSET = {k for k, _ in SECRET_KEYS}
_SCALAR_RE = re.compile(r'^([A-Za-z_][\w]*):\s*(.+?)\s*$')


def _is_vault(text):
    return text.lstrip().startswith("$ANSIBLE_VAULT")


def _unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def _read_values():
    """평문 vcs.yml 의 key/value 파싱. 없으면 {}, vault면 None."""
    if not os.path.isfile(VCS_YML):
        return {}
    with open(VCS_YML, "r", encoding="utf-8") as f:
        text = f.read()
    if _is_vault(text):
        return None
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _SCALAR_RE.match(s)
        if m:
            out[m.group(1)] = _unquote(m.group(2))
    return out


def status():
    vals = _read_values()
    if vals is None:  # vault 암호화됨
        return {
            "exists": True, "vault": True,
            "keys": [{"key": k, "label": lb, "set": True} for k, lb in SECRET_KEYS],
        }
    return {
        "exists": os.path.isfile(VCS_YML), "vault": False,
        "keys": [{"key": k, "label": lb, "set": bool(vals.get(k, "").strip())}
                 for k, lb in SECRET_KEYS],
    }


def write(values):
    """제공된 비어있지 않은 값만 갱신(빈 값은 기존 유지). (ok, message) 반환."""
    existing = _read_values()
    if existing is None:
        return False, "vcs.yml 이 ansible-vault로 암호화되어 있어 대시보드에서 편집할 수 없습니다. CLI로 편집하세요."

    for k, v in (values or {}).items():
        if k in _KEYSET and v is not None and str(v).strip() != "":
            existing[k] = str(v)

    if not existing:
        return False, "저장할 시크릿이 없습니다."

    d = os.path.dirname(VCS_YML)
    if not os.path.isdir(d):
        os.makedirs(d)

    lines = [
        "# LTE-R 시크릿 (대시보드 생성) — 커밋 금지(.gitignore).",
        "# 강화: ansible-vault encrypt ansible/inventory/group_vars/vcs.yml",
        "",
    ]
    # 알려진 키 먼저(정의 순서), 그 외 보존
    ordered = [k for k, _ in SECRET_KEYS if k in existing]
    ordered += [k for k in existing if k not in _KEYSET]
    for k in ordered:
        v = str(existing[k]).replace("\\", "\\\\").replace('"', '\\"')
        lines.append('{}: "{}"'.format(k, v))
    lines.append("")

    fd = os.open(VCS_YML, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, "\n".join(lines).encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(VCS_YML, 0o600)
    except OSError:
        pass
    return True, "{}개 시크릿 저장됨".format(len([k for k in _KEYSET if k in existing]))
