"""
inventory.py — 인벤토리 구조화 읽기/쓰기 (PyYAML 무의존)

대시보드의 인벤토리 편집 폼이 사용한다. hosts.ini 와 host_vars/<node>.yml 를
정해진 템플릿으로 파싱/재생성한다. 폐쇄망 운영자가 폼으로 안전하게 편집하도록
구조화 필드만 다루고, 저장 시 표준 템플릿(주석·NTP 플레이스홀더 포함)으로 재생성한다.

데이터 모델(JSON):
  {
    "group": {"ansible_user": "root", "asset_root": "/root/lter_vcs_gimhae"},
    "nodes": [
      {"name":"vcs-node1","ansible_host":"10.0.0.11","server_id":1,
       "peer_ip":"10.0.0.12","app_user":"vcs","ramdisk_size":"10g",
       "optical_nics":["ens1f0np0","ens1f1np1","ens4f0np0","ens4f1np1"]}
    ]
  }
"""
import os
import re

from .orchestrator import ANSIBLE_DIR

INV_DIR = os.path.join(ANSIBLE_DIR, "inventory")
HOSTS = os.path.join(INV_DIR, "hosts.ini")
HV_DIR = os.path.join(INV_DIR, "host_vars")

_NODE_RE = re.compile(r"^(\S+)\s+ansible_host=(\S+)")
_KV_RE = re.compile(r"^([A-Za-z_][\w]*)\s*=\s*(.+?)\s*$")
_SCALAR_RE = re.compile(r"^([A-Za-z_][\w]*):\s*(.+?)\s*$")
_LISTITEM_RE = re.compile(r"^\s*-\s*(.+?)\s*$")
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _strip_comment(val):
    return val.split("#", 1)[0].strip()


GROUP_OPTIONAL = ("ntp_ip1", "ntp_ip2")  # 비어있으면 hosts.ini 에 미기록


def read_inventory():
    group = {"ansible_user": "root", "asset_root": "/root/lter_vcs_gimhae",
             "ntp_ip1": "", "ntp_ip2": ""}
    hosts = []  # [(name, ansible_host)]
    if os.path.isfile(HOSTS):
        section = None
        with open(HOSTS, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1]
                    continue
                if section == "vcs":
                    m = _NODE_RE.match(line)
                    if m:
                        hosts.append((m.group(1), m.group(2)))
                elif section == "vcs:vars":
                    m = _KV_RE.match(line)
                    if m:
                        group[m.group(1)] = m.group(2)

    nodes = []
    for name, ahost in hosts:
        node = {
            "name": name, "ansible_host": ahost,
            "server_id": None, "peer_ip": "", "app_user": "vcs",
            "ramdisk_size": "10g", "optical_nics": [],
        }
        hv = os.path.join(HV_DIR, name + ".yml")
        if os.path.isfile(hv):
            _parse_host_vars(hv, node)
        nodes.append(node)
    return {"group": group, "nodes": nodes}


def _parse_host_vars(path, node):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    in_nics = False
    for raw in lines:
        if raw.lstrip().startswith("#"):
            in_nics = False
            continue
        if in_nics:
            m = _LISTITEM_RE.match(raw)
            if m and (raw.startswith(" ") or raw.startswith("\t")):
                node["optical_nics"].append(_strip_comment(m.group(1)))
                continue
            in_nics = False
        if raw.strip() == "optical_nics:":
            in_nics = True
            continue
        m = _SCALAR_RE.match(raw)
        if not m:
            continue
        key, val = m.group(1), _strip_comment(m.group(2))
        if key == "server_id":
            try:
                node["server_id"] = int(val)
            except ValueError:
                node["server_id"] = val
        elif key in ("peer_ip", "app_user", "ramdisk_size"):
            node[key] = val


def validate(model):
    """오류 메시지 리스트 반환(빈 리스트면 유효)."""
    errors = []
    nodes = model.get("nodes") or []
    if not nodes:
        errors.append("최소 1개 노드가 필요합니다.")
    seen = set()
    for i, n in enumerate(nodes):
        tag = "노드#{}".format(i + 1)
        name = (n.get("name") or "").strip()
        if not name or not _NAME_RE.match(name):
            errors.append("{}: 노드 이름이 비었거나 허용되지 않는 문자입니다.".format(tag))
        elif name in seen:
            errors.append("{}: 노드 이름 '{}' 중복.".format(tag, name))
        else:
            seen.add(name)
        if not (n.get("ansible_host") or "").strip():
            errors.append("{}({}): ansible_host 가 필요합니다.".format(tag, name))
        sid = n.get("server_id")
        if sid in (None, ""):
            errors.append("{}({}): server_id 가 필요합니다.".format(tag, name))
        else:
            try:
                int(sid)
            except (TypeError, ValueError):
                errors.append("{}({}): server_id 는 정수여야 합니다.".format(tag, name))
    return errors


def write_inventory(model):
    errors = validate(model)
    if errors:
        return errors

    group = model.get("group") or {}
    ansible_user = (group.get("ansible_user") or "root").strip()
    asset_root = (group.get("asset_root") or "/root/lter_vcs_gimhae").strip()
    nodes = model["nodes"]

    if not os.path.isdir(HV_DIR):
        os.makedirs(HV_DIR)

    # hosts.ini 재생성
    lines = [
        "# LTE-R 녹취서버 인벤토리 (2노드 HA) — 대시보드에서 생성/편집",
        "# 실제 IP/값은 현장에 맞게 수정. 비밀번호는 Ansible Vault 사용 권장.",
        "",
        "[vcs]",
    ]
    for n in nodes:
        lines.append("{} ansible_host={}".format(n["name"].strip(), n["ansible_host"].strip()))
    lines += ["", "[vcs:vars]", "ansible_user={}".format(ansible_user),
              "asset_root={}".format(asset_root)]
    for k in GROUP_OPTIONAL:
        v = (group.get(k) or "").strip()
        if v:
            lines.append("{}={}".format(k, v))
    lines.append("")
    with open(HOSTS, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # host_vars 재생성
    keep = set()
    for n in nodes:
        name = n["name"].strip()
        keep.add(name + ".yml")
        nics = [s.strip() for s in (n.get("optical_nics") or []) if s and s.strip()]
        role = "Active" if str(n.get("server_id")) == "1" else "Standby"
        body = [
            "# {} 호스트 변수 ({}) — 대시보드 생성".format(name, role),
            "# 플레이북 08-1/08-3/13/14/18 등이 참조하는 변수.",
            "server_id: {}".format(int(n["server_id"])),
            "peer_ip: {}          # 복제 상대".format((n.get("peer_ip") or "").strip()),
            "app_user: {}".format((n.get("app_user") or "vcs").strip()),
            "ramdisk_size: {}".format((n.get("ramdisk_size") or "10g").strip()),
            "# 광 NIC 명 (현장마다 다름 / 18 ringbuffer 파라미터화 대상)",
            "optical_nics:",
        ]
        for nic in nics:
            body.append("  - {}".format(nic))
        body += [
            "# 폐쇄망 NTP(04)는 그룹변수 ntp_ip1/ntp_ip2 로 설정(hosts.ini [vcs:vars] / 대시보드 인벤토리)",
            "",
        ]
        with open(os.path.join(HV_DIR, name + ".yml"), "w", encoding="utf-8") as f:
            f.write("\n".join(body))

    # 더 이상 노드가 아닌 host_vars 파일 제거(이전에 관리하던 것만)
    for fn in os.listdir(HV_DIR):
        if fn.endswith(".yml") and fn not in keep:
            os.remove(os.path.join(HV_DIR, fn))

    return []
