"""XGBoost models for ultrasonic defect classification, depth, and size regression.

Loads three .ubj models exported from the offline training pipeline:
  - xgb_classifier.ubj : multi-class internal-defect classifier
  - xgb_depth.ubj      : regressor for defect depth (mm below surface)
  - xgb_size.ubj       : regressor for defect lateral size (mm)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class InternalDefectPrediction:
    cls: str
    cls_confidence: float
    depth_mm: float
    size_mm: float


class XGBoostDefectModels:
    DEFAULT_CLASSES = (
        "void",
        "delamination",
        "inclusion",
        "porosity",
        "no_defect",
    )

    def __init__(
        self,
        classifier_path: str | Path,
        depth_path: str | Path,
        size_path: str | Path,
        classes: tuple[str, ...] | None = None,
    ):
        import xgboost as xgb  # lazy

        self.classes = classes or self.DEFAULT_CLASSES

        self.classifier = xgb.XGBClassifier()
        self.depth_model = xgb.XGBRegressor()
        self.size_model = xgb.XGBRegressor()

        for model, path in (
            (self.classifier, classifier_path),
            (self.depth_model, depth_path),
            (self.size_model, size_path),
        ):
            p = Path(path)
            if not p.exists():
                raise FileNotFoundError(f"XGBoost model not found: {p}")
            model.load_model(str(p))
            log.info("Loaded XGBoost model: %s", p.name)

    def predict(self, features: np.ndarray) -> InternalDefectPrediction:
        if features.ndim == 1:
            features = features.reshape(1, -1)

        proba = self.classifier.predict_proba(features)[0]
        cls_idx = int(np.argmax(proba))
        cls_name = self.classes[cls_idx] if cls_idx < len(self.classes) else f"cls{cls_idx}"
        cls_conf = float(proba[cls_idx])

        depth = float(self.depth_model.predict(features)[0])
        size = float(self.size_model.predict(features)[0])

        return InternalDefectPrediction(
            cls=cls_name,
            cls_confidence=cls_conf,
            depth_mm=max(0.0, depth),
            size_mm=max(0.0, size),
        )

    def predict_batch(self, features: np.ndarray) -> list[InternalDefectPrediction]:
        if features.ndim == 1:
            features = features.reshape(1, -1)
        proba = self.classifier.predict_proba(features)
        depths = self.depth_model.predict(features)
        sizes = self.size_model.predict(features)
        out: list[InternalDefectPrediction] = []
        for p, d, s in zip(proba, depths, sizes):
            idx = int(np.argmax(p))
            out.append(
                InternalDefectPrediction(
                    cls=self.classes[idx] if idx < len(self.classes) else f"cls{idx}",
                    cls_confidence=float(p[idx]),
                    depth_mm=max(0.0, float(d)),
                    size_mm=max(0.0, float(s)),
                )
            )
        return out
