"""
events.py — 공용 이벤트 버스 (스레드 기반 SSE 브로드캐스트)

Flask(동기) 환경에 맞춰 asyncio 대신 queue.Queue 로 구독자에게 푸시한다.
orchestrator(파이프라인)와 gitassets(자산 동기화)가 공유한다.
"""
import queue
import threading

from . import state


class EventBus(object):
    def __init__(self):
        self._subs = []
        self._lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=1000)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event):
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


bus = EventBus()


def emit(step_id, line, level="info"):
    """로그 1라인: SQLite 영속 + 실시간 브로드캐스트."""
    state.append_log(step_id, line, level)
    bus.publish({"type": "log", "step": step_id, "level": level, "line": line})


def emit_status(step_id, status, **extra):
    ev = {"type": "status", "step": step_id, "status": status}
    ev.update(extra)
    bus.publish(ev)


def emit_done(scope_status):
    bus.publish({"type": "done", "status": scope_status})
