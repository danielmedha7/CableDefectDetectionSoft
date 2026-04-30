"""YOLOv8 TensorRT defect detector.

Loads an exported YOLOv8n .engine file, runs warmup, and provides
per-frame inference returning structured Detection objects.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Detection:
    bbox: tuple[int, int, int, int]   # x1, y1, x2, y2
    cls: str
    confidence: float
    camera_id: int
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class YOLODetector:
    """Wraps an Ultralytics YOLO TensorRT engine for defect detection."""

    DEFAULT_CLASSES = (
        "crack",
        "scratch",
        "dent",
        "insulation_gap",
        "deformation",
        "contamination",
    )

    def __init__(
        self,
        engine_path: str | Path,
        classes: tuple[str, ...] | None = None,
        conf_threshold: float = 0.45,
        iou_threshold: float = 0.50,
        imgsz: int = 640,
        device: int | str = 0,
        half: bool = True,
        warmup_runs: int = 5,
    ):
        self.engine_path = Path(engine_path)
        self.classes = classes or self.DEFAULT_CLASSES
        self.conf = conf_threshold
        self.iou = iou_threshold
        self.imgsz = imgsz
        self.device = device
        self.half = half

        if not self.engine_path.exists():
            raise FileNotFoundError(f"YOLO engine not found: {self.engine_path}")

        from ultralytics import YOLO  # imported lazily

        log.info("Loading YOLO engine: %s", self.engine_path)
        self.model = YOLO(str(self.engine_path), task="detect")
        self._warmup(warmup_runs)

    def _warmup(self, runs: int) -> None:
        dummy = np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8)
        for _ in range(runs):
            self.model.predict(dummy, verbose=False, device=self.device, half=self.half)
        log.info("YOLO warmup complete (%d runs)", runs)

    def infer(self, frame: np.ndarray, camera_id: int) -> list[Detection]:
        """Run detection on a single frame; return list of Detection objects."""
        ts = time.time()
        results = self.model.predict(
            source=frame,
            conf=self.conf,
            iou=self.iou,
            imgsz=self.imgsz,
            device=self.device,
            half=self.half,
            verbose=False,
        )
        detections: list[Detection] = []
        if not results:
            return detections

        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return detections

        for box in boxes:
            cls_idx = int(box.cls.item())
            cls_name = self.classes[cls_idx] if cls_idx < len(self.classes) else f"cls{cls_idx}"
            x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
            detections.append(
                Detection(
                    bbox=(x1, y1, x2, y2),
                    cls=cls_name,
                    confidence=float(box.conf.item()),
                    camera_id=camera_id,
                    timestamp=ts,
                )
            )
        return detections

    def infer_batch(self, frames: list[np.ndarray], camera_ids: list[int]) -> list[list[Detection]]:
        if len(frames) != len(camera_ids):
            raise ValueError("frames and camera_ids must be same length")
        return [self.infer(f, cid) for f, cid in zip(frames, camera_ids)]
