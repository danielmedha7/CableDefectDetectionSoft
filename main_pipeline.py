"""CableVision OS — Main Pipeline Runner.

Orchestrates the per-frame inference cycle on the Jetson:

  ┌─────────┐   ┌──────────┐  ┌──────┐  ┌────────┐  ┌────────┐  ┌────┐  ┌───────┐
  │Encoder  │ + │MultiCam  │→ │ROI   │→ │ YOLO   │→ │Fusion+ │→ │QC  │→ │Alerts │
  │thread   │   │Receiver  │  │(opt) │  │(round  │  │Severity│  │    │  │(ZMQ + │
  │(GPIO)   │   │(GStream) │  │      │  │robin)  │  │+ depth │  │    │  │  WS)  │
  └─────────┘   └──────────┘  └──────┘  └────────┘  └────────┘  └────┘  └───────┘

Threads:
  - encoder reader (Jetson GPIO BCM5/BCM6) → updates `position_tracker`
  - capture (inside MultiCameraReceiver)    → produces FrameBundles into mp.Queue
  - inference (in this class)               → consumes FrameBundles, emits events
  - ZMQ publisher (inside AlertDispatcher)  → fan-out to backend bridge

CLI:
    python main_pipeline.py --cable C-001 --spec HV_35mm
    python main_pipeline.py --cable C-001 --spec HV_35mm --dry-run
"""
from __future__ import annotations

import argparse
import logging
import queue as _q
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
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s :: %(message)s",
)
log = logging.getLogger("cablevision")


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ─────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────
class CableVisionPipeline:

    def __init__(
        self,
        config: dict[str, Any],
        cable_id: str,
        cable_spec: str,
        operator: str,
        dry_run: bool = False,
    ):
        self.config = config
        self.cable_id = cable_id
        self.cable_spec = cable_spec
        self.operator = operator
        self.dry_run = dry_run

        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

        cam_cfg = config["cameras"]
        inf_cfg = config["inference"]
        fus_cfg = config["fusion"]
        enc_cfg = config["encoder"]
        ipc_cfg = config["ipc"]
        gpio_cfg = config.get("gpio", {})
        laser_cfg = config.get("laser", {})

        # ── core inference ──
        self.detector: YOLODetector | None = None
        if not dry_run:
            engine_path = Path(inf_cfg["yolo_engine"])
            if engine_path.exists():
                log.info("Initialising YOLO detector …")
                self.detector = YOLODetector(
                    engine_path=str(engine_path),
                    classes=tuple(inf_cfg["classes"]),
                    conf_threshold=inf_cfg["yolo_conf"],
                    iou_threshold=inf_cfg["yolo_iou"],
                    imgsz=inf_cfg["yolo_imgsz"],
                    half=inf_cfg.get("half_precision", True),
                    warmup_runs=inf_cfg.get("warmup_runs", 5),
                )
            else:
                log.warning(
                    "YOLO engine %s missing — running without object detection. "
                    "Pipeline will still receive frames and emit session events.",
                    engine_path,
                )

        self.fusion = MultiViewFusion(
            camera_angles_deg=tuple(cam_cfg["angles_deg"]),
            camera_fov_deg=cam_cfg["fov_deg"],
            iou_threshold=fus_cfg["iou_threshold"],
            dedup_window_deg=fus_cfg["dedup_window_deg"],
            frame_width_px=cam_cfg["resolution"][0],
        )

        self.severity = SeverityClassifier()
        self.qc = QCEngine(rules_path="configs/qc_rules.yaml", cable_spec=cable_spec)

        self.position = PositionTracker(
            pulses_per_rev=enc_cfg["pulses_per_rev"],
            wheel_diameter_mm=enc_cfg["wheel_diameter_mm"],
            direction=enc_cfg.get("direction", "forward"),
        )

        self.alerts = AlertDispatcher(zmq_pub_addr=ipc_cfg.get("zmq_pub_addr"))
        self.sessions = SessionManager()
        self.reports = ReportGenerator(output_dir="./data/reports")

        # ── lazy-imported hardware modules ──
        self.receiver = None        # MultiCameraReceiver
        self.encoder = None         # EncoderReader
        self.laser = None           # LaserTriangulator

        if not dry_run:
            self._init_hardware(cam_cfg, gpio_cfg, laser_cfg, enc_cfg)

        self.cam_count = cam_cfg["count"]
        self._round_robin = 0
        self._depth_profile: np.ndarray | None = None
        self._depth_lock = threading.RLock()

    # ── hardware init ──
    def _init_hardware(self, cam_cfg, gpio_cfg, laser_cfg, enc_cfg):
        from processing.image_processing.multi_camera_receiver import MultiCameraReceiver
        from processing.image_processing.laser_triangulation import (
            LaserTriangulator, TriangulationConfig,
        )
        from edge_devices.sensors.encoder_reader import EncoderReader, EncoderConfig

        try:
            self.receiver = MultiCameraReceiver(
                sensor_ids=tuple(cam_cfg["sensor_ids"]),
                width=cam_cfg["resolution"][0],
                height=cam_cfg["resolution"][1],
                fps=cam_cfg["fps"],
                led_pin=gpio_cfg.get("led_pin", 18),
                laser_pin=gpio_cfg.get("laser_pin", 11),
                laser_every_n=laser_cfg.get("every_nth_frame", 5),
                position_provider=lambda: self.position.position_m,
            )
        except Exception as exc:
            log.error("MultiCameraReceiver init failed: %s — capture disabled", exc)
            self.receiver = None

        try:
            self.laser = LaserTriangulator(
                baseline_path=laser_cfg.get("baseline_path"),
                config=TriangulationConfig(
                    theta_deg=laser_cfg.get("angle_deg", 30.0),
                    focal_px=cam_cfg.get("focal_length_px", 2714.0),
                    pixel_pitch_mm=cam_cfg.get("pixel_pitch_mm", 0.00112),
                    working_distance_mm=cam_cfg.get("working_distance_mm", 250.0),
                    hsv_lower=tuple(laser_cfg.get("hsv_lower", (55, 100, 100))),
                    hsv_upper=tuple(laser_cfg.get("hsv_upper", (85, 255, 255))),
                ),
                profile_width=cam_cfg["resolution"][0],
            )
        except Exception as exc:
            log.warning("LaserTriangulator init failed: %s — depth disabled", exc)
            self.laser = None

        try:
            self.encoder = EncoderReader(EncoderConfig(
                pin_a=gpio_cfg.get("encoder_a", 29),
                pin_b=gpio_cfg.get("encoder_b", 31),
                pulses_per_rev=enc_cfg["pulses_per_rev"],
                wheel_diameter_mm=enc_cfg["wheel_diameter_mm"],
                direction=enc_cfg.get("direction", "forward"),
                publish_enabled=False,        # backend gets encoder via Pi side, not Jetson
            ))
        except Exception as exc:
            log.warning("EncoderReader init failed: %s — position will be 0", exc)
            self.encoder = None

    # ── helpers ──
    def _next_camera(self) -> int:
        cam_id = self._round_robin % self.cam_count
        self._round_robin += 1
        return cam_id

    def _get_depth_profile(self) -> np.ndarray | None:
        with self._depth_lock:
            return None if self._depth_profile is None else self._depth_profile.copy()

    # ── per-bundle ──
    def _process_bundle(self, frames: dict[int, np.ndarray], laser_frame: bool) -> int:
        """Returns the number of defects emitted for this bundle."""
        # update depth profile from a laser frame
        if laser_frame and self.laser is not None and frames:
            cam0_frame = frames.get(0) or next(iter(frames.values()))
            try:
                profile = self.laser.process(cam0_frame)
                with self._depth_lock:
                    self._depth_profile = profile
            except Exception:
                log.exception("laser triangulation failed")

        # pick one camera to run YOLO on per cycle (round-robin)
        cam_id = self._next_camera()
        if cam_id not in frames or self.detector is None:
            return 0

        per_cam: dict[int, list[Detection]] = {
            cam_id: self.detector.infer(frames[cam_id], cam_id)
        }
        if not any(per_cam.values()):
            return 0

        position_m = self.position.position_m
        defects = self.fusion.fuse(
            per_cam,
            position_m=position_m,
            depth_profile=self._get_depth_profile(),
        )
        defects = self.severity.classify_batch(defects)

        active = self.sessions.active
        sid = active.id if active else None
        emitted = 0
        for d in defects:
            d.session_id = sid                          # ← critical: backend filters on this
            verdict = self.qc.evaluate_defect(d, cable_length_m=position_m)
            self.sessions.add_defect(d)
            payload = {
                "defect": d.to_dict(),
                "verdict": verdict.verdict,
                "reasons": verdict.reasons,
                "session_id": sid,
            }
            self.alerts.dispatch("defect_detected", payload)
            emitted += 1
            if verdict.verdict == VERDICT_FAIL:
                log.warning("FAIL trigger: %s @ %.2fm — %s",
                            d.cls, d.position_m, verdict.reasons)
        return emitted

    # ── inference loop ──
    def _inference_loop(self) -> None:
        log.info("Inference loop started")
        if self.receiver is None:
            log.warning("No receiver — inference loop idle")
            return
        while not self._stop.is_set():
            try:
                bundle = self.receiver.queue.get(timeout=0.5)
            except _q.Empty:
                continue
            try:
                self._process_bundle(bundle.frames, bundle.laser_frame)
            except Exception:
                log.exception("bundle processing failed")
        log.info("Inference loop stopped")

    # ── lifecycle ──
    def start(self) -> None:
        log.info("Pipeline starting · cable=%s spec=%s", self.cable_id, self.cable_spec)
        self.qc.reset()
        self.position.reset()
        sess = self.sessions.start(self.cable_id, self.cable_spec, self.operator)
        self.alerts.dispatch("session_started", {
            "session_id": sess.id,
            "cable_id": self.cable_id,
            "cable_spec": self.cable_spec,
        })

        if self.encoder is not None:
            try:
                self.encoder.start()
                # forward pulses → PositionTracker
                t = threading.Thread(target=self._encoder_pump, daemon=True, name="encoder-pump")
                t.start()
                self._threads.append(t)
            except Exception:
                log.exception("encoder start failed")

        if self.receiver is not None:
            try:
                self.receiver.start()
            except Exception:
                log.exception("receiver start failed")

        if not self.dry_run:
            t = threading.Thread(target=self._inference_loop, daemon=True, name="inference")
            t.start()
            self._threads.append(t)

    def _encoder_pump(self) -> None:
        last_pulses = 0
        while not self._stop.is_set():
            try:
                snap = self.encoder.snapshot() if self.encoder else None
                if snap is not None:
                    delta = snap.pulses - last_pulses
                    if delta:
                        self.position.add_pulses(delta)
                    last_pulses = snap.pulses
            except Exception:
                log.exception("encoder pump")
            time.sleep(0.05)

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        log.info("Pipeline stopping …")

        for t in self._threads:
            t.join(timeout=2.0)
        if self.receiver is not None:
            try: self.receiver.stop()
            except Exception: pass
        if self.encoder is not None:
            try: self.encoder.stop()
            except Exception: pass
        if self.laser is not None:
            try: self.laser.close()
            except Exception: pass

        cable_length_m = self.position.position_m
        final = self.qc.finalize(cable_length_m=cable_length_m)
        self.sessions.update_length(cable_length_m)
        ended = self.sessions.end(final.verdict, final.reasons)
        self.alerts.dispatch("session_ended", {
            "session_id": ended.id if ended else None,
            "verdict": final.verdict,
            "reasons": final.reasons,
            "stats": final.stats.__dict__ if final.stats else {},
        })

        if ended is not None:
            try:
                paths = self.reports.save(ended, formats=("json", "html"))
                log.info("Report written: %s", {k: str(v) for k, v in paths.items()})
            except Exception:
                log.exception("report write failed")

        self.alerts.close()
        log.info("Pipeline stopped. Verdict=%s length=%.2fm", final.verdict, cable_length_m)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────
def _install_signal_handlers(pipeline: CableVisionPipeline) -> None:
    def _handler(signum, frame):  # noqa: ARG001
        log.info("Signal %s — shutting down", signum)
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
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without camera/sensor I/O — useful on dev box")
    args = parser.parse_args()

    cfg = load_config(args.config)
    pipeline = CableVisionPipeline(cfg, args.cable, args.spec, args.operator, dry_run=args.dry_run)
    _install_signal_handlers(pipeline)
    pipeline.start()
    log.info("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    pipeline.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
