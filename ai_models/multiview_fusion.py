"""Multi-view fusion + depth merge.

Projects per-camera detections into a unified cylindrical (angle°, arc_mm)
coordinate system, runs cross-camera NMS to deduplicate, and merges laser
depth-profile data into each surviving defect.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any

import numpy as np

from .yolo_detector import Detection

log = logging.getLogger(__name__)


@dataclass
class WorldDefect:
    cls: str
    confidence: float
    angle_deg: float
    position_m: float
    bbox: tuple[int, int, int, int]
    camera_id: int
    depth_mm: float = 0.0
    area_mm2: float = 0.0
    severity: str = "low"
    timestamp: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MultiViewFusion:

    def __init__(
        self,
        camera_angles_deg: tuple[float, ...] = (0.0, 90.0, 180.0, 270.0),
        camera_fov_deg: float = 77.0,
        iou_threshold: float = 0.35,
        dedup_window_deg: float = 15.0,
        frame_width_px: int = 1920,
        cable_radius_mm: float = 17.5,
    ):
        self.cam_angles = camera_angles_deg
        self.fov = camera_fov_deg
        self.iou_threshold = iou_threshold
        self.dedup_window_deg = dedup_window_deg
        self.frame_width_px = frame_width_px
        self.cable_radius_mm = cable_radius_mm

    def project_to_cylinder(self, det: Detection) -> tuple[float, float]:
        """Map a detection's bbox center to (angle_deg, arc_mm) on the cable."""
        x1, _, x2, _ = det.bbox
        cx = (x1 + x2) * 0.5
        offset_norm = (cx - self.frame_width_px * 0.5) / (self.frame_width_px * 0.5)
        cam_base_angle = self.cam_angles[det.camera_id % len(self.cam_angles)]
        angle_deg = (cam_base_angle + offset_norm * (self.fov * 0.5)) % 360.0
        arc_mm = np.radians(angle_deg) * self.cable_radius_mm
        return angle_deg, float(arc_mm)

    @staticmethod
    def _angular_distance(a: float, b: float) -> float:
        d = abs(a - b) % 360.0
        return min(d, 360.0 - d)

    def cross_camera_nms(self, defects: list[WorldDefect]) -> list[WorldDefect]:
        if not defects:
            return []
        ordered = sorted(defects, key=lambda d: d.confidence, reverse=True)
        kept: list[WorldDefect] = []
        for cand in ordered:
            duplicate = False
            for k in kept:
                if cand.cls != k.cls:
                    continue
                if self._angular_distance(cand.angle_deg, k.angle_deg) <= self.dedup_window_deg:
                    duplicate = True
                    break
            if not duplicate:
                kept.append(cand)
        return kept

    def merge_depth(
        self,
        defect: WorldDefect,
        depth_profile: np.ndarray | None,
    ) -> WorldDefect:
        if depth_profile is None or depth_profile.size == 0:
            return defect
        x1, _, x2, _ = defect.bbox
        x1c = max(0, min(int(x1), depth_profile.size - 1))
        x2c = max(x1c + 1, min(int(x2), depth_profile.size))
        slab = depth_profile[x1c:x2c]
        slab = slab[np.isfinite(slab)]
        if slab.size:
            defect.depth_mm = float(np.mean(np.abs(slab)))
        return defect

    def fuse(
        self,
        per_camera_detections: dict[int, list[Detection]],
        position_m: float,
        depth_profile: np.ndarray | None = None,
    ) -> list[WorldDefect]:
        """Main entrypoint: turn per-camera Detection lists into deduplicated WorldDefects."""
        world_defects: list[WorldDefect] = []
        for cam_id, dets in per_camera_detections.items():
            for d in dets:
                angle_deg, _arc = self.project_to_cylinder(d)
                w = WorldDefect(
                    cls=d.cls,
                    confidence=d.confidence,
                    angle_deg=angle_deg,
                    position_m=position_m,
                    bbox=d.bbox,
                    camera_id=cam_id,
                    timestamp=d.timestamp,
                )
                world_defects.append(w)

        deduped = self.cross_camera_nms(world_defects)
        return [self.merge_depth(d, depth_profile) for d in deduped]
