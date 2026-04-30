"""Tracks the cable's longitudinal position from rotary encoder pulses."""
from __future__ import annotations

import logging
import math
import threading
import time

log = logging.getLogger(__name__)


class PositionTracker:
    """Converts encoder pulse counts into linear cable position in metres."""

    def __init__(
        self,
        pulses_per_rev: int = 2000,
        wheel_diameter_mm: float = 100.0,
        direction: str = "forward",
    ):
        if pulses_per_rev <= 0:
            raise ValueError("pulses_per_rev must be > 0")
        self.pulses_per_rev = pulses_per_rev
        self.circumference_mm = math.pi * wheel_diameter_mm
        self.mm_per_pulse = self.circumference_mm / pulses_per_rev
        self.sign = 1 if direction == "forward" else -1

        self._pulses: int = 0
        self._last_update_ts: float = time.time()
        self._last_pulses: int = 0
        self._lock = threading.RLock()

    def reset(self) -> None:
        with self._lock:
            self._pulses = 0
            self._last_pulses = 0
            self._last_update_ts = time.time()

    def add_pulses(self, delta: int) -> None:
        with self._lock:
            self._pulses += int(delta) * self.sign

    def set_pulses(self, total: int) -> None:
        with self._lock:
            self._pulses = int(total) * self.sign

    @property
    def position_mm(self) -> float:
        with self._lock:
            return self._pulses * self.mm_per_pulse

    @property
    def position_m(self) -> float:
        return self.position_mm / 1000.0

    def speed_mps(self) -> float:
        with self._lock:
            now = time.time()
            dt = now - self._last_update_ts
            if dt <= 0:
                return 0.0
            d_pulses = self._pulses - self._last_pulses
            self._last_pulses = self._pulses
            self._last_update_ts = now
            return (d_pulses * self.mm_per_pulse / 1000.0) / dt

    def snapshot(self) -> dict[str, float]:
        return {
            "position_m": self.position_m,
            "position_mm": self.position_mm,
            "pulses": float(self._pulses),
            "speed_mps": self.speed_mps(),
        }
