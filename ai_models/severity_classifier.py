"""Severity classification for cable defects.

Combines geometric features (depth, area, class) with model confidence
into a normalised severity score and discrete level.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .multiview_fusion import WorldDefect

log = logging.getLogger(__name__)


@dataclass
class SeverityResult:
    score: float            # 0.0–1.0
    level: str              # low | medium | high | critical
    reasons: list[str]


class SeverityClassifier:
    """Heuristic severity scoring; XGBoost depth/size models can override depth_mm/area_mm2."""

    CLASS_WEIGHTS = {
        "crack":          0.95,
        "insulation_gap": 1.00,
        "deformation":    0.85,
        "dent":           0.55,
        "scratch":        0.30,
        "contamination":  0.20,
    }

    def __init__(
        self,
        thresholds: dict[str, float] | None = None,
        depth_norm_mm: float = 2.0,
        area_norm_mm2: float = 30.0,
    ):
        self.thresholds = thresholds or {
            "low":      0.0,
            "medium":   0.50,
            "high":     0.75,
            "critical": 0.90,
        }
        self.depth_norm_mm = depth_norm_mm
        self.area_norm_mm2 = area_norm_mm2

    def _level_from_score(self, score: float) -> str:
        level = "low"
        for name in ("medium", "high", "critical"):
            if score >= self.thresholds[name]:
                level = name
        return level

    def score_defect(self, defect: WorldDefect) -> SeverityResult:
        reasons: list[str] = []
        cls_w = self.CLASS_WEIGHTS.get(defect.cls, 0.4)
        depth_term = min(defect.depth_mm / self.depth_norm_mm, 1.0) if defect.depth_mm else 0.0
        area_term = min(defect.area_mm2 / self.area_norm_mm2, 1.0) if defect.area_mm2 else 0.0
        conf_term = max(0.0, min(defect.confidence, 1.0))

        score = (
            0.35 * cls_w
            + 0.30 * depth_term
            + 0.20 * area_term
            + 0.15 * conf_term
        )
        score = max(0.0, min(score, 1.0))

        if cls_w >= 0.9:
            reasons.append(f"high-impact class:{defect.cls}")
        if depth_term >= 0.5:
            reasons.append(f"depth {defect.depth_mm:.2f}mm")
        if area_term >= 0.5:
            reasons.append(f"area {defect.area_mm2:.1f}mm²")
        if conf_term >= 0.85:
            reasons.append(f"high confidence {conf_term:.2f}")

        return SeverityResult(score=score, level=self._level_from_score(score), reasons=reasons)

    def classify(self, defect: WorldDefect) -> WorldDefect:
        result = self.score_defect(defect)
        defect.severity = result.level
        defect.extra["severity_score"] = result.score
        defect.extra["severity_reasons"] = result.reasons
        return defect

    def classify_batch(self, defects: list[WorldDefect]) -> list[WorldDefect]:
        return [self.classify(d) for d in defects]
