"""CableVision OS — Main Pipeline Runner.

Orchestrates the per-frame inference cycle:
  1. Camera capture (multiprocessing)
  2. ROI extraction
  3. YOLOv8 detection (round-robin per camera)
  4. Laser-depth merge (shared memory)
  5. Multi-view fusion + cross-camera NMS
  6. Severity classification
  7. QC evaluation
  8. Alert dispatch + async persistence

Run:
    python main_pipeline.py --config configs/config.yaml --cable C-001 --spec HV_35mm
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ai_models import (
    Detection,
    MultiViewFusion,
    SeverityClassifier,
    YOLODetector,
)
from system_logic import (
    AlertDispatcher,
    PositionTracker,
    QCEngine,
    ReportGenerator,
    SessionManager,
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_REVIEW,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s :: %(message)s",
)
log = logging.getLogger("cablevision")


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class CableVisionPipeline:

    def __init__(self, config: dict[str, Any], cable_id: str, cable_spec: str, operator: str):
        self.config = config
        self.cable_id = cable_id
        self.cable_spec = cable_spec
        self.operator = operator
        self._stop = threading.Event()

        cam_cfg = config["cameras"]
        inf_cfg = config["inference"]
        fus_cfg = config["fusion"]
        enc_cfg = config["encoder"]
        ipc_cfg = config["ipc"]

        log.info("Initialising YOLO detector …")
        self.detector = YOLODetector(
            engine_path=inf_cfg["yolo_engine"],
            classes=tuple(inf_cfg["classes"]),
            conf_threshold=inf_cfg["yolo_conf"],
            iou_threshold=inf_cfg["yolo_iou"],
            imgsz=inf_cfg["yolo_imgsz"],
            half=inf_cfg.get("half_precision", True),
            warmup_runs=inf_cfg.get("warmup_runs", 5),
        )

        self.fusion = MultiViewFusion(
            camera_angles_deg=tuple(cam_cfg["angles_deg"]),
            camera_fov_deg=cam_cfg["fov_deg"],
            iou_threshold=fus_cfg["iou_threshold"],
            dedup_window_deg=fus_cfg["dedup_window_deg"],
            frame_width_px=cam_cfg["resolution"][0],
        )

        self.severity = SeverityClassifier()

        self.qc = QCEngine(
            rules_path="configs/qc_rules.yaml",
            cable_spec=cable_spec,
        )

        self.position = PositionTracker(
            pulses_per_rev=enc_cfg["pulses_per_rev"],
            wheel_diameter_mm=enc_cfg["wheel_diameter_mm"],
            direction=enc_cfg.get("direction", "forward"),
        )

        self.alerts = AlertDispatcher(zmq_pub_addr=ipc_cfg.get("zmq_pub_addr"))
        self.sessions = SessionManager()
        self.reports = ReportGenerator(output_dir="./data/reports")

        self.cam_count = cam_cfg["count"]
        self._round_robin = 0
        self._depth_profile: np.ndarray | None = None
        self._depth_lock = threading.RLock()

    # ── camera & frame handling ──
    def update_depth_profile(self, profile: np.ndarray) -> None:
        with self._depth_lock:
            self._depth_profile = profile.astype(np.float32, copy=False)

    def _get_depth_profile(self) -> np.ndarray | None:
        with self._depth_lock:
            return None if self._depth_profile is None else self._depth_profile.copy()

    def _next_camera(self) -> int:
        cam_id = self._round_robin % self.cam_count
        self._round_robin += 1
        return cam_id

    # ── per-frame ──
    def process_frame_bundle(self, frames: dict[int, np.ndarray]) -> list[dict[str, Any]]:
        """Process one bundle (4 frames) and return any newly-emitted defect events."""
        cam_id = self._next_camera()
        if cam_id not in frames:
            return []

        per_cam: dict[int, list[Detection]] = {cam_id: self.detector.infer(frames[cam_id], cam_id)}
        if not any(per_cam.values()):
            return []

        position_m = self.position.position_m
        depth_profile = self._get_depth_profile()
        defects = self.fusion.fuse(per_cam, position_m=position_m, depth_profile=depth_profile)
        defects = self.severity.classify_batch(defects)

        emitted: list[dict[str, Any]] = []
        for d in defects:
            verdict = self.qc.evaluate_defect(d, cable_length_m=position_m)
            self.sessions.add_defect(d)
            payload = {
                "defect": d.to_dict(),
                "verdict": verdict.verdict,
                "reasons": verdict.reasons,
            }
            self.alerts.dispatch("defect_detected", payload)
            emitted.append(payload)
            if verdict.verdict == VERDICT_FAIL:
                log.warning("FAIL trigger: %s @ %.2fm — %s", d.cls, d.position_m, verdict.reasons)
        return emitted

    # ── lifecycle ──
    def start(self) -> None:
        log.info("Pipeline starting · cable=%s spec=%s", self.cable_id, self.cable_spec)
        self.qc.reset()
        self.position.reset()
        self.sessions.start(self.cable_id, self.cable_spec, self.operator)
        self.alerts.dispatch("session_started", {
            "cable_id": self.cable_id,
            "cable_spec": self.cable_spec,
        })

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        log.info("Pipeline stopping …")

        cable_length_m = self.position.position_m
        final = self.qc.finalize(cable_length_m=cable_length_m)
        self.sessions.update_length(cable_length_m)
        ended = self.sessions.end(final.verdict, final.reasons)
        self.alerts.dispatch("session_ended", {
            "verdict": final.verdict,
            "reasons": final.reasons,
            "stats": final.stats.__dict__ if final.stats else {},
        })

        if ended is not None:
            paths = self.reports.save(ended, formats=("json", "html"))
            log.info("Report written: %s", {k: str(v) for k, v in paths.items()})

        self.alerts.close()
        log.info("Pipeline stopped. Verdict=%s", final.verdict)


# ── CLI ──
def _install_signal_handlers(pipeline: CableVisionPipeline) -> None:
    def _handler(signum, frame):  # noqa: ARG001
        log.info("Signal %s received — shutting down", signum)
        pipeline.stop()
        sys.exit(0)
    signal.signal(signal.SIGINT, _handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handler)


def main() -> int:
    parser = argparse.ArgumentParser(description="CableVision OS pipeline runner")
    parser.add_argument("--config", default="configs/config.yaml")
    parser.add_argument("--cable", required=True, help="Cable ID, e.g. C-001")
    parser.add_argument("--spec", default="default", help="QC cable spec")
    parser.add_argument("--operator", default="system")
    parser.add_argument("--dry-run", action="store_true", help="Run without camera/sensor I/O")
    args = parser.parse_args()

    cfg = load_config(args.config)
    pipeline = CableVisionPipeline(cfg, args.cable, args.spec, args.operator)
    _install_signal_handlers(pipeline)
    pipeline.start()

    if args.dry_run:
        log.info("Dry-run: pipeline initialised; no live capture loop. Press Ctrl+C to exit.")
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
    else:
        log.info("Live capture loop is wired in processing/image_processing/multi_camera_receiver.py")
        log.info("Run that module to feed frames into pipeline.process_frame_bundle()")
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass

    pipeline.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
