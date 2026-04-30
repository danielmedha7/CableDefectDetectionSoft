"""Laser-line triangulation for surface depth profile.

Workflow per laser-frame:
  1. HSV mask the green laser stripe.
  2. Compute the laser-line centroid per column (sub-pixel, intensity-weighted).
  3. Subtract the calibrated baseline → pixel displacement.
  4. Triangulate displacement → depth in mm using camera-laser geometry.
  5. Write the depth profile (float32[W]) into multiprocessing.shared_memory
     so the inference layer can read it without copy.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from multiprocessing import shared_memory
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore


@dataclass
class TriangulationConfig:
    theta_deg: float = 30.0           # laser angle to surface
    focal_px: float = 2714.0          # IMX219 focal in pixels
    pixel_pitch_mm: float = 0.00112   # pixel pitch
    hsv_lower: tuple[int, int, int] = (55, 100, 100)
    hsv_upper: tuple[int, int, int] = (85, 255, 255)


class LaserTriangulator:

    def __init__(
        self,
        baseline_path: str | Path | None = None,
        config: TriangulationConfig | None = None,
        shm_name: str = "laser_depth_profile",
        profile_width: int = 640,
    ):
        if cv2 is None:
            raise RuntimeError("OpenCV is required for LaserTriangulator")
        self.config = config or TriangulationConfig()
        self.profile_width = profile_width
        self.shm_name = shm_name

        self._tan_theta = float(np.tan(np.radians(self.config.theta_deg)))
        self._hsv_lower = np.array(self.config.hsv_lower, dtype=np.uint8)
        self._hsv_upper = np.array(self.config.hsv_upper, dtype=np.uint8)

        self.baseline: np.ndarray | None = None
        if baseline_path is not None:
            p = Path(baseline_path)
            if p.exists():
                self.baseline = np.load(p).astype(np.float32)
                log.info("Loaded laser baseline (%d px) from %s", self.baseline.size, p)
            else:
                log.warning("Baseline file %s missing — using zero baseline", p)

        # shared memory for fusion-side reader
        self._shm: shared_memory.SharedMemory | None = None
        self._shm_arr: np.ndarray | None = None
        self._init_shm()

    def _init_shm(self) -> None:
        size = self.profile_width * np.dtype(np.float32).itemsize
        try:
            self._shm = shared_memory.SharedMemory(name=self.shm_name, create=True, size=size)
        except FileExistsError:
            self._shm = shared_memory.SharedMemory(name=self.shm_name, create=False, size=size)
        self._shm_arr = np.ndarray((self.profile_width,), dtype=np.float32, buffer=self._shm.buf)
        self._shm_arr[:] = 0.0

    # ── core math ──
    def extract_centroid(self, frame: np.ndarray) -> np.ndarray:
        """Return float32[W] of laser-line row centroid per column (NaN if absent)."""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self._hsv_lower, self._hsv_upper)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

        h, w = mask.shape
        ys = np.arange(h, dtype=np.float32)[:, None]
        weights = mask.astype(np.float32)
        col_sum = weights.sum(axis=0)
        valid = col_sum > 1.0
        centroids = np.full(w, np.nan, dtype=np.float32)
        # weighted mean row per column where valid
        weighted = (weights * ys).sum(axis=0)
        centroids[valid] = weighted[valid] / col_sum[valid]
        return centroids

    def triangulate(self, centroids: np.ndarray) -> np.ndarray:
        """Convert centroid array → depth_mm[W]."""
        if self.baseline is None or self.baseline.size != centroids.size:
            base = np.nanmedian(centroids) if np.any(np.isfinite(centroids)) else 0.0
            baseline = np.full_like(centroids, base, dtype=np.float32)
        else:
            baseline = self.baseline

        delta_px = baseline - centroids
        depth_mm = (delta_px * self.config.pixel_pitch_mm) / (self.config.focal_px * self._tan_theta * 1e-3)
        depth_mm = np.where(np.isfinite(depth_mm), depth_mm, 0.0).astype(np.float32)
        return depth_mm

    def process(self, frame: np.ndarray) -> np.ndarray:
        centroids = self.extract_centroid(frame)
        if centroids.size != self.profile_width:
            xp = np.linspace(0, 1, centroids.size)
            xq = np.linspace(0, 1, self.profile_width)
            centroids = np.interp(xq, xp, centroids).astype(np.float32)
        depth_mm = self.triangulate(centroids)
        self._write_shm(depth_mm)
        return depth_mm

    def _write_shm(self, depth_mm: np.ndarray) -> None:
        if self._shm_arr is None:
            return
        n = min(depth_mm.size, self._shm_arr.size)
        self._shm_arr[:n] = depth_mm[:n]

    # ── calibration helper ──
    def calibrate_baseline(self, flat_frames: list[np.ndarray], save_to: str | Path) -> np.ndarray:
        """Average centroid over N flat-reference frames; persist as baseline."""
        stack = np.stack([self.extract_centroid(f) for f in flat_frames], axis=0)
        baseline = np.nanmean(stack, axis=0).astype(np.float32)
        baseline = np.where(np.isfinite(baseline), baseline, np.nanmedian(baseline))
        Path(save_to).parent.mkdir(parents=True, exist_ok=True)
        np.save(save_to, baseline)
        self.baseline = baseline
        log.info("Saved laser baseline → %s (%d px)", save_to, baseline.size)
        return baseline

    def close(self) -> None:
        try:
            if self._shm is not None:
                self._shm.close()
                try:
                    self._shm.unlink()
                except FileNotFoundError:
                    pass
        finally:
            self._shm = None
            self._shm_arr = None
