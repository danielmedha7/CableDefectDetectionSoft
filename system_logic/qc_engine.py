"""Config-driven QC rules engine.

Evaluates WorldDefect events against per-cable-spec thresholds defined in
qc_rules.yaml and emits a verdict (PASS / FAIL / REVIEW) plus reason list.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ai_models.multiview_fusion import WorldDefect

log = logging.getLogger(__name__)


VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_REVIEW = "REVIEW"


@dataclass
class QCStats:
    total_defects: int = 0
    by_class: dict[str, int] = field(default_factory=dict)
    by_severity: dict[str, int] = field(default_factory=dict)
    max_depth_mm: float = 0.0
    max_area_mm2: float = 0.0
    cable_length_m: float = 0.0


@dataclass
class QCVerdict:
    verdict: str
    reasons: list[str] = field(default_factory=list)
    stats: QCStats | None = None


class QCEngine:

    def __init__(self, rules_path: str | Path, cable_spec: str = "default"):
        self.rules_path = Path(rules_path)
        self._rules = self._load_rules()
        self.cable_spec = cable_spec
        self.stats = QCStats()
        self._terminal_verdict: str | None = None
        self._terminal_reasons: list[str] = []

    def _load_rules(self) -> dict:
        with self.rules_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if "cable_specs" not in data:
            raise ValueError(f"qc_rules missing 'cable_specs': {self.rules_path}")
        return data

    def reload(self) -> None:
        self._rules = self._load_rules()
        log.info("QC rules reloaded from %s", self.rules_path)

    def set_cable_spec(self, spec: str) -> None:
        if spec not in self._rules["cable_specs"]:
            raise KeyError(f"unknown cable_spec '{spec}'")
        self.cable_spec = spec
        log.info("QC cable spec set to: %s", spec)

    def reset(self) -> None:
        self.stats = QCStats()
        self._terminal_verdict = None
        self._terminal_reasons = []

    @property
    def spec(self) -> dict:
        return self._rules["cable_specs"][self.cable_spec]

    @property
    def verdict_cfg(self) -> dict:
        return self._rules.get("verdict", {})

    def evaluate_defect(self, defect: WorldDefect, cable_length_m: float | None = None) -> QCVerdict:
        """Evaluate a single defect; updates running stats and may set a terminal verdict."""
        self.stats.total_defects += 1
        self.stats.by_class[defect.cls] = self.stats.by_class.get(defect.cls, 0) + 1
        self.stats.by_severity[defect.severity] = self.stats.by_severity.get(defect.severity, 0) + 1
        self.stats.max_depth_mm = max(self.stats.max_depth_mm, defect.depth_mm)
        self.stats.max_area_mm2 = max(self.stats.max_area_mm2, defect.area_mm2)
        if cable_length_m is not None:
            self.stats.cable_length_m = cable_length_m

        reasons: list[str] = []
        verdict = VERDICT_PASS
        spec = self.spec
        cfg = self.verdict_cfg

        if cfg.get("fail_on_forbidden_class", True):
            if defect.cls in spec.get("forbidden_classes", []):
                reasons.append(f"forbidden class: {defect.cls}")
                verdict = VERDICT_FAIL

        if cfg.get("fail_on_critical_severity", True) and defect.severity == "critical":
            reasons.append(f"critical severity defect: {defect.cls}")
            verdict = VERDICT_FAIL

        max_depth = spec.get("max_crack_depth_mm")
        if max_depth is not None and defect.depth_mm > max_depth:
            reasons.append(f"depth {defect.depth_mm:.2f}mm exceeds {max_depth}mm")
            verdict = VERDICT_FAIL

        max_area = spec.get("max_area_mm2")
        if max_area is not None and defect.area_mm2 > max_area:
            reasons.append(f"area {defect.area_mm2:.1f}mm² exceeds {max_area}mm²")
            verdict = VERDICT_FAIL

        if verdict != VERDICT_FAIL:
            if cfg.get("review_on_high_severity", True) and defect.severity == "high":
                verdict = VERDICT_REVIEW
                reasons.append(f"high severity: {defect.cls}")
            density_threshold = cfg.get("review_if_defect_density_above")
            limit = spec.get("max_defects_per_100m")
            if density_threshold and limit and self.stats.cable_length_m > 0:
                per_100m = self.stats.total_defects / max(self.stats.cable_length_m / 100.0, 1e-9)
                if per_100m >= density_threshold * limit and verdict == VERDICT_PASS:
                    verdict = VERDICT_REVIEW
                    reasons.append(f"density {per_100m:.2f}/100m near limit {limit}")

        if verdict == VERDICT_FAIL:
            self._terminal_verdict = VERDICT_FAIL
            self._terminal_reasons.extend(reasons)
        elif verdict == VERDICT_REVIEW and self._terminal_verdict != VERDICT_FAIL:
            self._terminal_verdict = VERDICT_REVIEW
            self._terminal_reasons.extend(reasons)

        return QCVerdict(verdict=verdict, reasons=reasons, stats=self.stats)

    def finalize(self, cable_length_m: float) -> QCVerdict:
        """Compute the session-end verdict using accumulated stats."""
        self.stats.cable_length_m = cable_length_m
        spec = self.spec
        reasons = list(self._terminal_reasons)
        verdict = self._terminal_verdict or VERDICT_PASS

        limit = spec.get("max_defects_per_100m")
        if limit and cable_length_m > 0:
            per_100m = self.stats.total_defects / max(cable_length_m / 100.0, 1e-9)
            if per_100m > limit:
                verdict = VERDICT_FAIL
                reasons.append(f"defect density {per_100m:.2f}/100m exceeds {limit}/100m")

        return QCVerdict(verdict=verdict, reasons=reasons, stats=self.stats)
