"""
report.py — 설치 사이클 결과 보고서 엔진 (Markdown / HTML, stdlib only)

한 사이클의 전 단계 status/검증결과/멱등성/실행이력을 집계해 보고서를 만든다.
폐쇄망(py3.6)이므로 외부 템플릿 엔진 없이 문자열로 생성한다.

집계 소스:
  - pipeline.STEPS  : step 메타(phase/playbook/verify_cmd/idempotent/required_vars)
  - state.get_all_status() : step별 현재 status/changed/ok/failed/verify/시각
  - state.get_runs() : 최근 실행 이력
"""
import time

from . import pipeline, state
from .orchestrator import MODE

_STATUS_LABEL = {
    "success": "성공", "failed": "실패", "running": "실행중",
    "pending": "대기", "skipped": "스킵",
}
_STATUS_ICON = {
    "success": "✅", "failed": "❌", "running": "🔄",
    "pending": "⬜", "skipped": "⏭️",
}


def _fmt_ts(ts):
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _fmt_dur(a, b):
    if not a or not b:
        return "-"
    s = int(b - a)
    return "{}m{}s".format(s // 60, s % 60) if s >= 60 else "{}s".format(s)


def _md(v):
    """Markdown 표 셀 안전화: 파이프/개행 이스케이프."""
    return str(v).replace("|", "\\|").replace("\n", " ")


def _idem_label(st):
    """step의 멱등성 2회 검사 결과 라벨."""
    if not st["idempotent"]:
        return "N/A"
    idem = st.get("idem")
    if not idem:
        return "미검사"
    if idem == "ok":
        return "✅ changed=0"
    if idem == "fail2":
        return "❌ 2회차실패"
    if idem.startswith("regress:"):
        return "⚠ 회귀(chg={})".format(idem.split(":", 1)[1])
    return idem


def collect():
    """보고서용 구조화 데이터 집계."""
    statuses = state.get_all_status()
    summary = {"success": 0, "failed": 0, "running": 0, "pending": 0, "skipped": 0}
    phases = []
    idem_checked = idem_clean = 0

    for key, label in pipeline.PHASES:
        rows = []
        for st in pipeline.steps_for_phase(key):
            cur = statuses.get(st.id, {})
            status = cur.get("status", "pending")
            summary[status] = summary.get(status, 0) + 1
            changed = cur.get("changed", 0) or 0
            idem = cur.get("idem")
            if st.idempotent and idem:
                idem_checked += 1
                if idem == "ok":
                    idem_clean += 1
            rows.append({
                "id": st.id, "name": st.name, "playbook": st.playbook,
                "status": status, "changed": changed,
                "ok": cur.get("ok", 0) or 0, "failed": cur.get("failed", 0) or 0,
                "verify_cmd": st.verify_cmd, "verify": cur.get("verify"),
                "idempotent": st.idempotent, "idem": idem,
                "required_vars": st.required_vars,
                "started": cur.get("started"), "ended": cur.get("ended"),
            })
        phases.append({"key": key, "label": label, "steps": rows})

    total = sum(summary.values())
    done = summary["success"]
    overall = "success"
    if summary["failed"]:
        overall = "failed"
    elif summary["running"]:
        overall = "running"
    elif summary["pending"] == total:
        overall = "pending"
    elif summary["pending"]:
        overall = "partial"

    return {
        "mode": MODE,
        "generated_at": _fmt_ts(time.time()),
        "summary": summary,
        "total": total, "done": done,
        "overall": overall,
        "idempotency": {"checked": idem_checked, "clean": idem_clean},
        "phases": phases,
        "runs": state.get_runs(10),
    }


def to_markdown(data=None):
    d = data or collect()
    s = d["summary"]
    L = []
    L.append("# LTE-R 녹취서버 설치 결과 보고서")
    L.append("")
    L.append("- 생성 시각: **{}**".format(d["generated_at"]))
    L.append("- 실행 모드: `{}`".format(d["mode"]))
    L.append("- 종합 판정: **{}**".format(_STATUS_LABEL.get(d["overall"], d["overall"])))
    L.append("- 진행: **{}/{}** 성공 (실패 {}, 대기 {}, 스킵 {})".format(
        s["success"], d["total"], s["failed"], s["pending"], s["skipped"]))
    idem = d["idempotency"]
    L.append("- 멱등성 게이트: 검사 {} 중 changed=0 **{}**".format(idem["checked"], idem["clean"]))
    L.append("")

    for ph in d["phases"]:
        L.append("## {}".format(ph["label"]))
        L.append("")
        L.append("| 상태 | Step | 단계 | Playbook | ok/chg/fail | 멱등성 | 검증 |")
        L.append("|------|------|------|----------|-------------|--------|------|")
        for st in ph["steps"]:
            icon = _STATUS_ICON.get(st["status"], "")
            verify = "`{}`".format(_md(st["verify_cmd"])) if st["verify_cmd"] else "-"
            L.append("| {} {} | {} | {} | `{}` | {}/{}/{} | {} | {} |".format(
                icon, _STATUS_LABEL.get(st["status"], st["status"]),
                st["id"], _md(st["name"]), _md(st["playbook"]),
                st["ok"], st["changed"], st["failed"], _idem_label(st), verify))
        L.append("")

    L.append("## 실행 이력")
    L.append("")
    if d["runs"]:
        L.append("| # | scope | target | mode | 시작 | 소요 | 결과 |")
        L.append("|---|-------|--------|------|------|------|------|")
        for r in d["runs"]:
            L.append("| {} | {} | {} | {} | {} | {} | {} |".format(
                r["id"], r.get("scope", "-"), r.get("target", "-"), r.get("mode", "-"),
                _fmt_ts(r.get("started")), _fmt_dur(r.get("started"), r.get("ended")),
                _STATUS_LABEL.get(r.get("status"), r.get("status", "-"))))
    else:
        L.append("_실행 이력 없음_")
    L.append("")
    return "\n".join(L)


_CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,'Malgun Gothic',sans-serif;
  max-width:1000px;margin:24px auto;padding:0 18px;color:#1c2530;background:#fff}
h1{border-bottom:2px solid #2b6cb0;padding-bottom:6px}
h2{margin-top:28px;color:#2b6cb0}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}
th,td{border:1px solid #d6dde6;padding:5px 8px;text-align:left}
th{background:#eef3f8}
code{background:#f1f4f8;padding:1px 4px;border-radius:3px;font-size:12px}
.badge{display:inline-block;padding:2px 10px;border-radius:12px;font-weight:600;font-size:13px}
.b-success{background:#d8f5e3;color:#176c3a}.b-failed{background:#fbdfe4;color:#9b1c32}
.b-running{background:#dcebfb;color:#5a2a9b}.b-partial{background:#fff3cd;color:#8a6d00}
.b-pending{background:#e9edf2;color:#566}
.s-success{color:#176c3a}.s-failed{color:#9b1c32;font-weight:600}
.s-running{color:#5a2a9b}.s-pending{color:#8a96a3}.s-skipped{color:#8a96a3}
.meta li{margin:2px 0}
"""


def _esc(v):
    return (str(v) if v is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_html(data=None):
    d = data or collect()
    s = d["summary"]
    idem = d["idempotency"]
    H = ['<!doctype html><html lang="ko"><head><meta charset="utf-8">',
         "<title>LTE-R 설치 결과 보고서</title><style>", _CSS, "</style></head><body>"]
    H.append("<h1>LTE-R 녹취서버 설치 결과 보고서</h1>")
    H.append('<span class="badge b-{}">{}</span>'.format(
        d["overall"], _STATUS_LABEL.get(d["overall"], d["overall"])))
    H.append('<ul class="meta">')
    H.append("<li>생성 시각: <b>{}</b></li>".format(_esc(d["generated_at"])))
    H.append("<li>실행 모드: <code>{}</code></li>".format(_esc(d["mode"])))
    H.append("<li>진행: <b>{}/{}</b> 성공 — 실패 {}, 대기 {}, 스킵 {}</li>".format(
        s["success"], d["total"], s["failed"], s["pending"], s["skipped"]))
    H.append("<li>멱등성 게이트: 검사 {} 중 changed=0 <b>{}</b></li>".format(
        idem["checked"], idem["clean"]))
    H.append("</ul>")

    for ph in d["phases"]:
        H.append("<h2>{}</h2>".format(_esc(ph["label"])))
        H.append("<table><tr><th>상태</th><th>Step</th><th>단계</th>"
                 "<th>Playbook</th><th>ok/chg/fail</th><th>멱등성</th>"
                 "<th>소요</th><th>검증 커맨드</th></tr>")
        for st in ph["steps"]:
            verify = "<code>{}</code>".format(_esc(st["verify_cmd"])) if st["verify_cmd"] else "-"
            H.append("<tr>"
                     '<td class="s-{0}">{1} {2}</td><td>{3}</td><td>{4}</td>'
                     "<td><code>{5}</code></td><td>{6}/{7}/{8}</td><td>{9}</td>"
                     "<td>{10}</td><td>{11}</td></tr>".format(
                         st["status"], _STATUS_ICON.get(st["status"], ""),
                         _STATUS_LABEL.get(st["status"], st["status"]),
                         _esc(st["id"]), _esc(st["name"]), _esc(st["playbook"]),
                         st["ok"], st["changed"], st["failed"], _esc(_idem_label(st)),
                         _fmt_dur(st["started"], st["ended"]), verify))
        H.append("</table>")

    H.append("<h2>실행 이력</h2>")
    if d["runs"]:
        H.append("<table><tr><th>#</th><th>scope</th><th>target</th><th>mode</th>"
                 "<th>시작</th><th>소요</th><th>결과</th></tr>")
        for r in d["runs"]:
            H.append("<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
                     '<td>{}</td><td>{}</td><td class="s-{}">{}</td></tr>'.format(
                         r["id"], _esc(r.get("scope")), _esc(r.get("target")), _esc(r.get("mode")),
                         _fmt_ts(r.get("started")), _fmt_dur(r.get("started"), r.get("ended")),
                         r.get("status"), _STATUS_LABEL.get(r.get("status"), r.get("status"))))
        H.append("</table>")
    else:
        H.append("<p><i>실행 이력 없음</i></p>")
    H.append("</body></html>")
    return "".join(H)
