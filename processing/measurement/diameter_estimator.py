"""Cable diameter estimator.

Two paths for estimating the running cable diameter:

  1. **Vision-based** — silhouette edge detection on each camera frame; the
     pixel width is converted to mm using camera intrinsics + nominal
     working distance. Diameters from the 4 cameras are fused (median).

  2. **Laser-based** — uses the laser depth profile to find the cable
     surface envelope; diameter = max-min of envelope (corrected for
     baseline). Higher-precision when laser is calibrated.

Output: `DiameterEstimate(diameter_mm, deviation_pct, per_camera_mm, source)`.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Deque

import numpy as np

log = logging.getLogger(__name__)

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore


@dataclass
class DiameterEstimate:
    diameter_mm: float
    deviation_pct: float
    per_camera_mm: dict[int, float] = field(default_factory=dict)
    source: str = "vision"            # 'vision' | 'laser' | 'fused'


class DiameterEstimator:

    def __init__(
        self,
        expected_diameter_mm: float,
        focal_length_px: float = 2714.0,
        working_distance_mm: float = 250.0,
        pixel_pitch_mm: float = 0.00112,
        smoothing_window: int = 12,
        max_dev_pct_clamp: float = 25.0,
    ):
        self.expected = expected_diameter_mm
        self.focal_px = focal_length_px
        self.work_dist = working_distance_mm
        self.pixel_pitch = pixel_pitch_mm
        self.max_dev_clamp = max_dev_pct_clamp
        # mm per pixel at the cable plane
        self.mm_per_pixel = (working_distance_mm * pixel_pitch_mm) / (focal_length_px * pixel_pitch_mm) \
            if focal_length_px > 0 else 1.0
        self.mm_per_pixel = working_distance_mm / focal_length_px  # equivalent simple ratio
        self._history: Deque[float] = deque(maxlen=smoothing_window)

    # ── vision-based ──
    def _diameter_from_frame(self, frame: np.ndarray) -> float | None:
        if cv2 is None or frame is None:
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        edges = cv2.Canny(gray, 60, 180)
        col_sum = edges.sum(axis=0)
        thresh = col_sum.max() * 0.30 if col_sum.max() > 0 else 0
        if thresh == 0:
            return None
        cols = np.where(col_sum >= thresh)[0]
        if cols.size < 4:
            return None
        width_px = cols.max() - cols.min()
        return float(width_px * self.mm_per_pixel)

    def estimate_vision(self, frames: dict[int, np.ndarray]) -> DiameterEstimate:
        per_cam: dict[int, float] = {}
        for cam_id, frm in frames.items():
            d = self._diameter_from_frame(frm)
            if d is not None and d > 0:
                per_cam[cam_id] = d

        if not per_cam:
            return DiameterEstimate(diameter_mm=0.0, deviation_pct=0.0, source="vision")

        median_d = float(np.median(list(per_cam.values())))
        return self._finalise(median_d, per_cam, "vision")

    # ── laser-based ──
    def estimate_from_depth(self, depth_profile_mm: np.ndarray) -> DiameterEstimate:
        if depth_profile_mm.size == 0:
            return DiameterEstimate(0.0, 0.0, source="laser")
        finite = depth_profile_mm[np.isfinite(depth_profile_mm)]
        if finite.size < 4:
            return DiameterEstimate(0.0, 0.0, source="laser")
        # surface envelope = peak-to-peak, corrected by adding nominal diameter offset
        envelope = float(np.percentile(finite, 99) - np.percentile(finite, 1))
        diameter = self.expected + envelope  # depth profile is signed displacement
        return self._finalise(diameter, {}, "laser")

    # ── fused ──
    def estimate(
        self,
        frames: dict[int, np.ndarray] | None = None,
        depth_profile_mm: np.ndarray | None = None,
    ) -> DiameterEstimate:
        v = self.estimate_vision(frames) if frames else None
        l = self.estimate_from_depth(depth_profile_mm) if depth_profile_mm is not None else None

        if v and l and v.diameter_mm > 0 and l.diameter_mm > 0:
            fused = 0.4 * v.diameter_mm + 0.6 * l.diameter_mm  # trust laser more
            return self._finalise(fused, v.per_camera_mm, "fused")
        return v or l or DiameterEstimate(0.0, 0.0, source="vision")

    # ── helpers ──
    def _finalise(self, d_mm: float, per_cam: dict[int, float], source: str) -> DiameterEstimate:
        self._history.append(d_mm)
        smoothed = float(np.median(self._history))
        dev_pct = abs(smoothed - self.expected) / self.expected * 100.0 if self.expected > 0 else 0.0
        dev_pct = min(dev_pct, self.max_dev_clamp)
        return DiameterEstimate(
            diameter_mm=smoothed,
            deviation_pct=dev_pct,
            per_camera_mm=per_cam,
            source=source,
        )

    def reset(self) -> None:
        self._history.clear()
