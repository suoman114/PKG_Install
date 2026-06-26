"""
assetcheck.py — 자산 사전점검 (asset_root 에 필요한 RPM/파일이 있는지 확인)

플레이북이 `{{ asset_root }}/...` 로 참조하는 자산(컨트롤러 로컬 경로)이 실제로
존재하는지 실행 전에 확인한다. git 동기화/USB 복사 등 어떤 방식으로 자산을 두든,
누락을 조기에 잡아 설치 도중 실패를 막는다.

각 step 의 pipeline.Step.required_assets(asset_root 기준 상대경로) 를 사용한다.
경로가 '/' 로 끝나면 "비어있지 않은 디렉토리"를 요구한다(예: rpm/openjdk/).
"""
import os

from . import inventory, pipeline

DEFAULT_ROOT = "/root/lter_vcs_gimhae"


def asset_root():
    g = inventory.read_inventory().get("group", {})
    return (g.get("asset_root") or DEFAULT_ROOT).strip()


def _dir_has_files(path):
    if not os.path.isdir(path):
        return False
    for _r, _d, names in os.walk(path):
        if names:
            return True
    return False


def _exists(root, rel):
    p = os.path.join(root, rel)
    if rel.endswith("/"):
        return _dir_has_files(p)
    return os.path.exists(p)


def required_for(step_ids):
    out = []
    for sid in step_ids:
        st = pipeline.get_step(sid)
        if not st:
            continue
        for a in st.required_assets:
            if a not in out:
                out.append(a)
    return out


def check(step_ids=None):
    """step_ids 미지정 시 전체 step 의 자산을 점검."""
    if step_ids is None:
        step_ids = pipeline.all_step_ids()
    root = asset_root()
    root_ok = os.path.isdir(root)
    items = []
    for rel in required_for(step_ids):
        items.append({
            "path": rel,
            "ok": bool(root_ok) and _exists(root, rel),
            "is_dir": rel.endswith("/"),
        })
    return {
        "asset_root": root,
        "root_exists": root_ok,
        "items": items,
        "missing": [it["path"] for it in items if not it["ok"]],
    }
