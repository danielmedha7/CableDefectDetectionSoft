"""Roundness / ovality monitor.

Compares the per-camera diameter estimates around the cable to detect
deformation (out-of-round / oval cross-section). Roundness ratio R is
defined as min(d_i)/max(d_i) over the 4 viewing angles — 1.0 = perfect
circle, lower = more deformed.

Also exposes:
  * `ovality_pct` — (d_max - d_min)/d_mean × 100
  * Trigger flag if R falls below a config threshold (e.g. 0.97)
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Deque

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class RoundnessReading:
    roundness: float            # 0.0–1.0
    ovality_pct: float
    d_min_mm: float
    d_max_mm: float
    d_mean_mm: float
    triggered: bool
    per_camera_mm: dict[int, float] = field(default_factory=dict)


class RoundnessMonitor:

    def __init__(
        self,
        min_roundness: float = 0.97,
        smoothing_window: int = 16,
        n_cameras: int = 4,
    ):
        self.min_roundness = min_roundness
        self.n_cameras = n_cameras
        self._history: Deque[float] = deque(maxlen=smoothing_window)
        self._trigger_count = 0

    def evaluate(self, per_camera_mm: dict[int, float]) -> RoundnessReading | None:
        values = [v for v in per_camera_mm.values() if v > 0]
        if len(values) < 2:
            return None

        arr = np.asarray(values, dtype=np.float32)
        d_min = float(arr.min())
        d_max = float(arr.max())
        d_mean = float(arr.mean())
        roundness = d_min / d_max if d_max > 0 else 0.0
        ovality = ((d_max - d_min) / d_mean) * 100.0 if d_mean > 0 else 0.0

        self._history.append(roundness)
        smoothed = float(np.median(self._history))

        triggered = smoothed < self.min_roundness
        if triggered:
            self._trigger_count += 1
        else:
            self._trigger_count = 0

        return RoundnessReading(
            roundness=smoothed,
            ovality_pct=ovality,
            d_min_mm=d_min,
            d_max_mm=d_max,
            d_mean_mm=d_mean,
            triggered=triggered,
            per_camera_mm=dict(per_camera_mm),
        )

    @property
    def consecutive_triggers(self) -> int:
        return self._trigger_count

    def reset(self) -> None:
        self._history.clear()
        self._trigger_count = 0
