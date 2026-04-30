"""Internal defect detector (ultrasonic).

Pulls A-scan windows from the ultrasonic acquirer, runs them through the
feature extractor, and queries the XGBoost models to classify internal
defects (void / delamination / inclusion / porosity) plus their depth and
lateral size.

Emits `InternalDefect` records with cable position from the encoder, ready
to be fed into the QC engine alongside surface defects.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, asdict, field
from typing import Any, Callable, Deque

import numpy as np

from .feature_extractor import UltrasonicFeatureExtractor

log = logging.getLogger(__name__)


@dataclass
class InternalDefect:
    cls: str
    confidence: float
    depth_mm: float
    size_mm: float
    position_m: float
    channel: int
    timestamp: float
    severity: str = "low"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InternalDefectDetector:

    def __init__(
        self,
        xgb_models,                         # ai_models.XGBoostDefectModels
        feature_extractor: UltrasonicFeatureExtractor | None = None,
        no_defect_class: str = "no_defect",
        min_confidence: float = 0.55,
        cooldown_window: int = 8,           # ignore repeats within this many samples / channel
        position_provider: Callable[[], float] | None = None,
    ):
        self.models = xgb_models
        self.fx = feature_extractor or UltrasonicFeatureExtractor()
        self.no_defect_class = no_defect_class
        self.min_confidence = min_confidence
        self.position_provider = position_provider
        self._recent: dict[int, Deque[tuple[str, float]]] = {}
        self._cooldown_window = cooldown_window

    # ── core ──
    def _is_recent_dup(self, channel: int, cls: str, position_m: float) -> bool:
        buf = self._recent.setdefault(channel, deque(maxlen=self._cooldown_window))
        for prev_cls, prev_pos in buf:
            if prev_cls == cls and abs(prev_pos - position_m) < 0.05:  # 50 mm
                return True
        buf.append((cls, position_m))
        return False

    def process_signal(
        self,
        signal: np.ndarray,
        channel: int = 0,
        timestamp: float | None = None,
    ) -> InternalDefect | None:
        import time
        ts = timestamp if timestamp is not None else time.time()
        feats = self.fx.extract(signal)
        pred = self.models.predict(feats)

        if pred.cls == self.no_defect_class or pred.cls_confidence < self.min_confidence:
            return None

        position_m = 0.0
        if self.position_provider is not None:
            try:
                position_m = float(self.position_provider())
            except Exception:
                pass

        if self._is_recent_dup(channel, pred.cls, position_m):
            return None

        return InternalDefect(
            cls=pred.cls,
            confidence=pred.cls_confidence,
            depth_mm=pred.depth_mm,
            size_mm=pred.size_mm,
            position_m=position_m,
            channel=channel,
            timestamp=ts,
        )

    def process_multichannel(
        self,
        signals_by_channel: dict[int, np.ndarray],
        timestamp: float | None = None,
    ) -> list[InternalDefect]:
        out: list[InternalDefect] = []
        for ch, sig in signals_by_channel.items():
            d = self.process_signal(sig, channel=ch, timestamp=timestamp)
            if d is not None:
                out.append(d)
        return out

    def reset(self) -> None:
        self._recent.clear()
