"""Fan-out alerts for QC events.

Pluggable backends — log, ZeroMQ pub, in-memory subscribers (for the
WebSocket bridge). Add SMS/email channels here.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)


class AlertDispatcher:

    def __init__(self, zmq_pub_addr: str | None = None):
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._lock = threading.RLock()
        self._zmq_socket: Any = None
        if zmq_pub_addr:
            try:
                import zmq
                ctx = zmq.Context.instance()
                self._zmq_socket = ctx.socket(zmq.PUB)
                self._zmq_socket.bind(zmq_pub_addr)
                log.info("ZMQ PUB bound at %s", zmq_pub_addr)
            except Exception as exc:
                log.warning("ZMQ unavailable, alerts will only log: %s", exc)
                self._zmq_socket = None

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def dispatch(self, event_type: str, payload: dict[str, Any]) -> None:
        envelope = {
            "type": event_type,
            "ts": time.time(),
            "payload": payload,
        }
        log.info("ALERT %s :: %s", event_type, payload)

        if self._zmq_socket is not None:
            try:
                self._zmq_socket.send_string(f"{event_type} {json.dumps(envelope, default=str)}")
            except Exception as exc:
                log.warning("ZMQ send failed: %s", exc)

        with self._lock:
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(envelope)
            except Exception:
                log.exception("subscriber callback raised")

    def dispatch_batch(self, events: Iterable[tuple[str, dict[str, Any]]]) -> None:
        for evt, payload in events:
            self.dispatch(evt, payload)

    def close(self) -> None:
        if self._zmq_socket is not None:
            try:
                self._zmq_socket.close(linger=0)
            except Exception:
                pass
            self._zmq_socket = None
