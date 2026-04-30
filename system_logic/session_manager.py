"""Inspection session lifecycle.

A session = one inspection run for one cable. Owns: session ID, start/end
timestamps, accumulated defects, cable spec, and QC verdict.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

from ai_models.multiview_fusion import WorldDefect

log = logging.getLogger(__name__)


@dataclass
class Session:
    id: str
    cable_id: str
    cable_spec: str
    operator: str
    started_at: float
    ended_at: float | None = None
    verdict: str | None = None
    verdict_reasons: list[str] = field(default_factory=list)
    cable_length_m: float = 0.0
    defects: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SessionManager:

    def __init__(self):
        self._active: Session | None = None
        self._history: list[Session] = []
        self._lock = threading.RLock()

    def start(
        self,
        cable_id: str,
        cable_spec: str = "default",
        operator: str = "system",
    ) -> Session:
        with self._lock:
            if self._active is not None:
                log.warning("starting new session while %s active; auto-ending", self._active.id)
                self._end_active(verdict="ABORTED", reasons=["new session started"])
            session = Session(
                id=uuid.uuid4().hex[:12],
                cable_id=cable_id,
                cable_spec=cable_spec,
                operator=operator,
                started_at=time.time(),
            )
            self._active = session
            log.info("Session %s started (cable=%s spec=%s)", session.id, cable_id, cable_spec)
            return session

    def add_defect(self, defect: WorldDefect) -> None:
        with self._lock:
            if self._active is None:
                log.warning("add_defect called with no active session — dropping")
                return
            self._active.defects.append(defect.to_dict())

    def update_length(self, cable_length_m: float) -> None:
        with self._lock:
            if self._active is not None:
                self._active.cable_length_m = cable_length_m

    def _end_active(self, verdict: str, reasons: list[str]) -> Session | None:
        if self._active is None:
            return None
        self._active.ended_at = time.time()
        self._active.verdict = verdict
        self._active.verdict_reasons = reasons
        self._history.append(self._active)
        ended = self._active
        self._active = None
        log.info("Session %s ended verdict=%s reasons=%s", ended.id, verdict, reasons)
        return ended

    def end(self, verdict: str, reasons: list[str] | None = None) -> Session | None:
        with self._lock:
            return self._end_active(verdict=verdict, reasons=reasons or [])

    @property
    def active(self) -> Session | None:
        with self._lock:
            return self._active

    def history(self, limit: int | None = None) -> list[Session]:
        with self._lock:
            items = list(self._history)
        return items[-limit:] if limit else items
