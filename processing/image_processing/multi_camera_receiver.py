"""Multi-camera frame receiver.

Owns the synchronised 4-camera capture loop on the Jetson side.
Builds GStreamer pipelines using nvarguscamerasrc for hardware-accelerated
MIPI CSI-2 capture and produces FrameBundle objects (one per cycle) into a
multiprocessing.Queue that the main pipeline consumes.

LED strobe + laser gate are pulsed via GPIO around each capture.
Every Nth bundle is flagged as a `laser_frame` so the LaserTriangulator
can extract the depth profile.
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class FrameBundle:
    """Synchronised capture from all cameras for a single cycle."""
    frames: dict[int, np.ndarray]      # {camera_id: BGR ndarray}
    timestamp: float
    encoder_pos_m: float = 0.0
    laser_frame: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class _DummyGPIO:
    """Stand-in for Jetson.GPIO when not running on Jetson hardware."""
    BOARD = "BOARD"
    OUT = "OUT"
    LOW = 0
    HIGH = 1

    def setmode(self, *_): pass
    def setup(self, *_, **__): pass
    def output(self, *_): pass
    def cleanup(self): pass


def _load_gpio():
    try:
        import Jetson.GPIO as GPIO  # type: ignore
        return GPIO
    except Exception:
        log.warning("Jetson.GPIO not available — using dummy stub")
        return _DummyGPIO()


class MultiCameraReceiver:

    def __init__(
        self,
        sensor_ids: tuple[int, ...] = (0, 1, 2, 3),
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        led_pin: int = 18,
        laser_pin: int = 11,
        queue_size: int = 8,
        laser_every_n: int = 5,
        position_provider=None,        # callable -> position_m
    ):
        self.sensor_ids = sensor_ids
        self.width = width
        self.height = height
        self.fps = fps
        self.led_pin = led_pin
        self.laser_pin = laser_pin
        self.laser_every_n = max(1, laser_every_n)
        self.position_provider = position_provider

        self.queue: mp.Queue = mp.Queue(maxsize=queue_size)
        self._caps: list[Any] = []
        self._stop = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._frame_idx = 0

        self._gpio = _load_gpio()
        self._init_gpio()
        self._open_cameras()

    # ── GStreamer / GPIO setup ──
    def _gst_pipeline(self, sensor_id: int) -> str:
        return (
            f"nvarguscamerasrc sensor-id={sensor_id} ! "
            f"video/x-raw(memory:NVMM), width=(int){self.width}, height=(int){self.height}, "
            f"format=(string)NV12, framerate=(fraction){self.fps}/1 ! "
            "nvvidconv ! video/x-raw, format=(string)BGRx ! "
            "videoconvert ! video/x-raw, format=(string)BGR ! appsink drop=1"
        )

    def _open_cameras(self) -> None:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("OpenCV (cv2) is required") from exc

        for sid in self.sensor_ids:
            cap = cv2.VideoCapture(self._gst_pipeline(sid), cv2.CAP_GSTREAMER)
            if not cap.isOpened():
                log.warning("Camera sensor-id=%d failed to open via GStreamer", sid)
            self._caps.append(cap)
        log.info("Opened %d cameras (sensors=%s)", len(self._caps), self.sensor_ids)

    def _init_gpio(self) -> None:
        try:
            self._gpio.setmode(self._gpio.BOARD)
            self._gpio.setup(self.led_pin,   self._gpio.OUT, initial=self._gpio.LOW)
            self._gpio.setup(self.laser_pin, self._gpio.OUT, initial=self._gpio.LOW)
        except Exception as exc:
            log.warning("GPIO init failed: %s", exc)

    # ── capture cycle ──
    def grab_bundle(self, laser_frame: bool = False) -> FrameBundle | None:
        try:
            self._gpio.output(self.led_pin, self._gpio.HIGH)
            if laser_frame:
                self._gpio.output(self.laser_pin, self._gpio.HIGH)

            ts = time.time()
            frames: dict[int, np.ndarray] = {}
            for cam_id, cap in enumerate(self._caps):
                ok, frm = cap.read() if cap is not None else (False, None)
                if ok and frm is not None:
                    frames[cam_id] = frm
                else:
                    log.debug("camera %d returned no frame", cam_id)
        finally:
            self._gpio.output(self.led_pin,   self._gpio.LOW)
            self._gpio.output(self.laser_pin, self._gpio.LOW)

        if not frames:
            return None

        pos_m = 0.0
        if self.position_provider is not None:
            try:
                pos_m = float(self.position_provider())
            except Exception:
                pass

        return FrameBundle(
            frames=frames,
            timestamp=ts,
            encoder_pos_m=pos_m,
            laser_frame=laser_frame,
        )

    def _capture_loop(self) -> None:
        log.info("Capture loop started")
        while not self._stop.is_set():
            self._frame_idx += 1
            laser = (self._frame_idx % self.laser_every_n == 0)
            bundle = self.grab_bundle(laser_frame=laser)
            if bundle is None:
                time.sleep(0.005)
                continue
            try:
                self.queue.put(bundle, timeout=0.5)
            except Exception:
                # queue full — drop oldest by reading once
                try:
                    self.queue.get_nowait()
                    self.queue.put_nowait(bundle)
                except Exception:
                    pass
        log.info("Capture loop stopped")

    # ── lifecycle ──
    def start(self) -> None:
        if self._capture_thread and self._capture_thread.is_alive():
            return
        self._stop.clear()
        self._capture_thread = threading.Thread(target=self._capture_loop, name="MCR-capture", daemon=True)
        self._capture_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._capture_thread:
            self._capture_thread.join(timeout=2.0)
        for cap in self._caps:
            try:
                cap.release()
            except Exception:
                pass
        try:
            self._gpio.cleanup()
        except Exception:
            pass
        log.info("MultiCameraReceiver shut down")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc_info):
        self.stop()
