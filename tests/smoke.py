#!/usr/bin/env python3
"""
smoke.py — 대시보드 백엔드 회귀 스모크 테스트 (의존성: Flask만, pytest 불필요)

CI와 폐쇄망 현장 자가검증 양쪽에서 사용한다. 실행:
    python tests/smoke.py
임시 DB/ansible 복사본에서만 동작하므로 저장소를 더럽히지 않는다.
실패 시 비정상 종료(exit!=0)한다.
"""
import json
import os
import shutil
import stat
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

_PASS = []
_FAIL = []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print("  {} {}{}".format("✓" if cond else "✗", name, "" if cond else "  -> " + detail))


def main():
    tmp = tempfile.mkdtemp(prefix="pkg_smoke_")
    ans = os.path.join(tmp, "ansible")
    shutil.copytree(os.path.join(REPO, "ansible"), ans)
    os.environ["DASHBOARD_MODE"] = "mock"
    os.environ["DASHBOARD_DB"] = os.path.join(tmp, "smoke.db")
    os.environ["ANSIBLE_DIR"] = ans
    os.environ["ASSET_DEST"] = os.path.join(tmp, "assets")

    from backend.app import app
    from backend import pipeline
    c = app.test_client()

    print("[1] pipeline / status")
    p = c.get("/api/pipeline").get_json()
    check("pipeline returns all steps", len(p["steps"]) == len(pipeline.all_step_ids()),
          "got %d" % len(p["steps"]))
    check("mode is mock", p["mode"] == "mock")

    print("[2] run an OS phase to success")
    c.post("/api/run", json={"scope": "phase", "id": "OS", "idempotency": True})
    os_ids = ("00", "01", "02", "03", "04", "05")
    deadline = time.time() + 60
    while time.time() < deadline:
        st = c.get("/api/status").get_json()["steps"]
        if all(st[k]["status"] in ("success", "failed") for k in os_ids) and st["05"].get("idem"):
            break
        time.sleep(0.3)
    st = c.get("/api/status").get_json()["steps"]
    check("all OS steps success", all(st[k]["status"] == "success" for k in os_ids),
          str({k: st[k]["status"] for k in os_ids}))
    check("idempotent steps recorded changed=0 (idem=ok)",
          all(st[k].get("idem") == "ok" for k in ("01", "02", "03", "04", "05")),
          str({k: st[k].get("idem") for k in os_ids}))
    check("non-idempotent 00 not idem-checked", st["00"].get("idem") in (None, ""))

    print("[3] report MD/HTML")
    rep = c.get("/api/report").get_json()
    check("report idempotency gate 5/5", rep["idempotency"] == {"checked": 5, "clean": 5},
          str(rep["idempotency"]))
    md = c.get("/api/report.md").get_data(as_text=True)
    check("MD has report title + idempotency col", "# LTE-R" in md and "멱등성" in md)
    check("MD tables pipe-safe", "\\|" in md)
    html = c.get("/api/report.html").get_data(as_text=True)
    check("HTML renders table", "<table>" in html and "멱등성" in html)

    print("[4] inventory round-trip (incl NTP group var)")
    inv = c.get("/api/inventory").get_json()
    check("2 nodes parsed", len(inv["nodes"]) == 2)
    m = json.loads(json.dumps(inv))
    m["nodes"][0]["peer_ip"] = "10.7.7.2"
    m["group"]["ntp_ip1"] = "10.50.0.1"
    r = c.post("/api/inventory", json=m)
    check("inventory save ok", r.status_code == 200, str(r.status_code))
    inv2 = c.get("/api/inventory").get_json()
    check("peer_ip persisted", inv2["nodes"][0]["peer_ip"] == "10.7.7.2")
    check("ntp_ip1 persisted", inv2["group"]["ntp_ip1"] == "10.50.0.1")
    hosts = open(os.path.join(ans, "inventory", "hosts.ini")).read()
    check("hosts.ini has ntp_ip1", "ntp_ip1=10.50.0.1" in hosts)
    bad = c.post("/api/inventory", json={"nodes": []})
    check("empty inventory rejected", bad.status_code == 400, str(bad.status_code))

    print("[5] secrets are masked + persisted 0600")
    s0 = c.get("/api/secrets").get_json()
    check("secret keys exposed (>=7)", len(s0["keys"]) >= 7, str(len(s0["keys"])))
    r = c.post("/api/secrets", json={"mariadb_root_password": "S3cr3t!", "vcs_os_password": "Os#1"})
    body = json.dumps(r.get_json())
    check("secret values never returned by API", "S3cr3t!" not in body and "Os#1" not in body)
    flags = {k["key"]: k["set"] for k in r.get_json()["keys"]}
    check("set flags reflect saved keys", flags["mariadb_root_password"] and flags["vcs_os_password"]
          and not flags["ssh_password"])
    vp = os.path.join(ans, "inventory", "group_vars", "vcs.yml")
    check("vcs.yml written 0600", os.path.isfile(vp) and oct(stat.S_IMODE(os.stat(vp).st_mode)) == "0o600")

    print("[6] git asset config (no network)")
    c.post("/api/git/config", json={"git_url": "file:///x/repo.git", "git_branch": "main"})
    g = c.get("/api/git").get_json()
    check("git config persisted", g["git_url"] == "file:///x/repo.git" and g["git_branch"] == "main")

    print("[7b] git auth masked + node connectivity check")
    r = c.post("/api/git/config", json={"git_url": "https://g/r.git", "git_user": "u", "git_token": "TOKxyz"})
    gd = r.get_json()
    check("git_auth set, token never returned",
          gd.get("git_auth") is True and "TOKxyz" not in json.dumps(gd) and "git_token" not in gd)
    gs = c.post("/api/git/stop").get_json()
    check("git stop idle is no-op", gs.get("stopping") is False, str(gs))
    iv = c.get("/api/inventory").get_json()
    # 127.0.0.1 → 즉시 refused(빠름). 연결확인은 mock에서도 실제 TCP를 찌른다.
    for n in iv["nodes"]:
        n["ansible_host"] = "127.0.0.1"
    c.post("/api/inventory", json=iv)
    nc = c.post("/api/nodes/check", json={"target": "all"})
    check("node check started", nc.status_code == 200, str(nc.status_code))
    deadline = time.time() + 20
    plog = []
    while time.time() < deadline:
        plog = [l["line"] for l in c.get("/api/logs?after_id=0").get_json()["logs"] if l["step_id"] == "ping"]
        if any("연결 확인 종료" in x for x in plog):
            break
        time.sleep(0.2)
    check("node check really probes (✓/✗ per node)",
          any(("TCP 연결 가능" in x or "연결 불가" in x) for x in plog), str(plog[-2:]))

    print("[7] file editor (list/read/write/guards)")
    fpaths = [f["path"] for f in c.get("/api/files").get_json()["files"]]
    check("playbooks listed, secret excluded",
          "playbooks/3_sysctl.yml" in fpaths and "inventory/group_vars/vcs.yml" not in fpaths)
    hv = "inventory/host_vars/vcs-node1.yml"
    cont = c.get("/api/files/content?path=" + hv).get_json()["content"]
    w = c.post("/api/files/content", json={"path": hv, "content": cont + "\ncustom_flag: true\n"})
    check("valid edit saved", w.status_code == 200, str(w.status_code))
    bad = c.post("/api/files/content", json={"path": "playbooks/3_sysctl.yml", "content": "x: [oops\n"})
    check("invalid YAML rejected", bad.status_code == 400, str(bad.status_code))
    g1 = c.get("/api/files/content?path=inventory/group_vars/vcs.yml").status_code
    g2 = c.get("/api/files/content?path=../../etc/passwd").status_code
    check("secret + traversal guarded", g1 == 400 and g2 == 400, "%s/%s" % (g1, g2))

    shutil.rmtree(tmp, ignore_errors=True)

    print("\n결과: %d passed, %d failed" % (len(_PASS), len(_FAIL)))
    if _FAIL:
        print("실패: " + ", ".join(_FAIL))
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
