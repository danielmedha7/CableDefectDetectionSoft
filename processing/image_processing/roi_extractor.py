"""ROI extractor.

Locates the cable in each frame and crops to a 640×640 patch suitable for
the YOLO input layer. Two strategies, in order of preference:

  1. Hough-circle on a downsampled grayscale view (works well when the cable
     is approximately circular in the frame, i.e. cross-cuts).
  2. Vertical edge profile fallback (works for longitudinal views — the
     cable is a vertical band; centre = mean column of strong edges).

Returns an `ROIResult` with the cropped patch, source bbox, and the affine
needed to project bbox coordinates back into the original frame.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore


@dataclass
class ROIResult:
    patch: np.ndarray              # cropped 640×640 (or `out_size`) BGR
    bbox: tuple[int, int, int, int]  # x1,y1,x2,y2 in original frame
    scale: float                   # patch_pixel = orig_pixel * scale
    method: str                    # 'hough' | 'edge' | 'center'

    def to_orig(self, x: float, y: float) -> tuple[float, float]:
        """Map a coordinate in patch space back to the original frame."""
        return self.bbox[0] + x / self.scale, self.bbox[1] + y / self.scale


class ROIExtractor:

    def __init__(
        self,
        out_size: int = 640,
        min_radius_px: int = 80,
        max_radius_px: int = 600,
        edge_lo: int = 60,
        edge_hi: int = 180,
        pad_ratio: float = 0.10,
    ):
        if cv2 is None:
            raise RuntimeError("OpenCV is required for ROIExtractor")
        self.out_size = out_size
        self.min_r = min_radius_px
        self.max_r = max_radius_px
        self.edge_lo = edge_lo
        self.edge_hi = edge_hi
        self.pad_ratio = pad_ratio

    def _hough_circle(self, gray: np.ndarray) -> tuple[int, int, int] | None:
        blurred = cv2.medianBlur(gray, 5)
        circles = cv2.HoughCircles(
            blurred,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=max(self.min_r, 50),
            param1=120,
            param2=40,
            minRadius=self.min_r,
            maxRadius=self.max_r,
        )
        if circles is None:
            return None
        circles = np.round(circles[0]).astype(int)
        # pick the strongest (first returned)
        cx, cy, r = circles[0]
        return int(cx), int(cy), int(r)

    def _edge_band(self, gray: np.ndarray) -> tuple[int, int, int, int] | None:
        edges = cv2.Canny(gray, self.edge_lo, self.edge_hi)
        col_sum = edges.sum(axis=0)
        if col_sum.max() == 0:
            return None
        thresh = col_sum.max() * 0.3
        cols = np.where(col_sum >= thresh)[0]
        if cols.size < 10:
            return None
        x1 = int(cols.min())
        x2 = int(cols.max())
        h = gray.shape[0]
        return x1, 0, x2, h

    def _center_crop(self, h: int, w: int) -> tuple[int, int, int, int]:
        side = min(h, w)
        x1 = (w - side) // 2
        y1 = (h - side) // 2
        return x1, y1, x1 + side, y1 + side

    def _square_with_pad(
        self, x1: int, y1: int, x2: int, y2: int, h: int, w: int
    ) -> tuple[int, int, int, int]:
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        side = max(x2 - x1, y2 - y1)
        side = int(side * (1.0 + self.pad_ratio))
        side = max(side, self.out_size // 2)
        half = side // 2
        nx1 = max(0, cx - half)
        ny1 = max(0, cy - half)
        nx2 = min(w, cx + half)
        ny2 = min(h, cy + half)
        # re-square if clipped
        s = min(nx2 - nx1, ny2 - ny1)
        return nx1, ny1, nx1 + s, ny1 + s

    def extract(self, frame: np.ndarray) -> ROIResult:
        if frame is None or frame.size == 0:
            raise ValueError("empty frame")
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        method = "center"
        bbox: tuple[int, int, int, int]

        circ = self._hough_circle(gray)
        if circ is not None:
            cx, cy, r = circ
            r_pad = int(r * (1.0 + self.pad_ratio))
            bbox = (
                max(0, cx - r_pad),
                max(0, cy - r_pad),
                min(w, cx + r_pad),
                min(h, cy + r_pad),
            )
            method = "hough"
        else:
            band = self._edge_band(gray)
            if band is not None:
                x1, y1, x2, y2 = band
                bbox = self._square_with_pad(x1, y1, x2, y2, h, w)
                method = "edge"
            else:
                bbox = self._center_crop(h, w)

        x1, y1, x2, y2 = bbox
        if x2 - x1 < 16 or y2 - y1 < 16:
            bbox = self._center_crop(h, w)
            x1, y1, x2, y2 = bbox
            method = "center"

        crop = frame[y1:y2, x1:x2]
        scale = self.out_size / max(crop.shape[0], crop.shape[1])
        patch = cv2.resize(crop, (self.out_size, self.out_size), interpolation=cv2.INTER_AREA)

        return ROIResult(patch=patch, bbox=(x1, y1, x2, y2), scale=scale, method=method)

    def extract_batch(self, frames: dict[int, np.ndarray]) -> dict[int, ROIResult]:
        return {cam_id: self.extract(f) for cam_id, f in frames.items()}
