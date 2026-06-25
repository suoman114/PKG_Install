"""
state.py — 설치 파이프라인 상태/로그 저장소 (SQLite, stdlib only)

폐쇄망/CentOS7(py3.6) 제약을 고려해 외부 ORM 없이 표준 sqlite3만 사용한다.
- step_status: step별 현재 상태/카운트/검증결과
- logs:        step별 로그 라인(영속)
- runs:        실행 이력(scope/대상/시각/상태)
"""
import os
import sqlite3
import threading
import time

DB_PATH = os.environ.get(
    "DASHBOARD_DB",
    os.path.join(os.path.dirname(__file__), "state.db"),
)

_lock = threading.Lock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row


def init_db():
    with _lock:
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS step_status (
                step_id   TEXT PRIMARY KEY,
                status    TEXT NOT NULL DEFAULT 'pending',
                changed   INTEGER DEFAULT 0,
                ok        INTEGER DEFAULT 0,
                failed    INTEGER DEFAULT 0,
                started   REAL,
                ended     REAL,
                verify    TEXT
            );
            CREATE TABLE IF NOT EXISTS logs (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      REAL NOT NULL,
                step_id TEXT,
                level   TEXT,
                line    TEXT
            );
            CREATE TABLE IF NOT EXISTS runs (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                scope   TEXT,
                target  TEXT,
                mode    TEXT,
                started REAL,
                ended   REAL,
                status  TEXT
            );
            """
        )
        _conn.commit()


def seed_steps(step_ids):
    """파이프라인 정의의 step들을 pending으로 초기 등록(없을 때만)."""
    with _lock:
        for sid in step_ids:
            _conn.execute(
                "INSERT OR IGNORE INTO step_status(step_id, status) VALUES (?, 'pending')",
                (sid,),
            )
        _conn.commit()


def set_status(step_id, status, **fields):
    cols = ["status"]
    vals = [status]
    for k in ("changed", "ok", "failed", "started", "ended", "verify"):
        if k in fields:
            cols.append(k)
            vals.append(fields[k])
    assignment = ", ".join("{}=?".format(c) for c in cols)
    with _lock:
        _conn.execute(
            "INSERT OR IGNORE INTO step_status(step_id) VALUES (?)", (step_id,)
        )
        _conn.execute(
            "UPDATE step_status SET {} WHERE step_id=?".format(assignment),
            vals + [step_id],
        )
        _conn.commit()


def get_all_status():
    with _lock:
        rows = _conn.execute("SELECT * FROM step_status").fetchall()
    return {r["step_id"]: dict(r) for r in rows}


def append_log(step_id, line, level="info"):
    with _lock:
        _conn.execute(
            "INSERT INTO logs(ts, step_id, level, line) VALUES (?,?,?,?)",
            (time.time(), step_id, level, line),
        )
        _conn.commit()


def get_logs(after_id=0, limit=2000):
    with _lock:
        rows = _conn.execute(
            "SELECT * FROM logs WHERE id > ? ORDER BY id ASC LIMIT ?",
            (after_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def reset_all(step_ids):
    with _lock:
        _conn.execute("DELETE FROM logs")
        _conn.execute("UPDATE step_status SET status='pending', changed=0, ok=0, "
                      "failed=0, started=NULL, ended=NULL, verify=NULL")
        _conn.commit()
    seed_steps(step_ids)


def start_run(scope, target, mode):
    with _lock:
        cur = _conn.execute(
            "INSERT INTO runs(scope, target, mode, started, status) "
            "VALUES (?,?,?,?, 'running')",
            (scope, target, mode, time.time()),
        )
        _conn.commit()
        return cur.lastrowid


def end_run(run_id, status):
    with _lock:
        _conn.execute(
            "UPDATE runs SET ended=?, status=? WHERE id=?",
            (time.time(), status, run_id),
        )
        _conn.commit()
