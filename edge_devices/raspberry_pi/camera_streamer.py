"""Raspberry Pi camera streamer.

Each Raspberry Pi owns two cameras and pushes timestamped JPEG frames to the
Jetson ingest endpoint. The Jetson side can reconstruct synchronised bundles
using node_id, camera_id, frame_id, and timestamp_ns.
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CameraConfig:
    camera_id: str
    name: str
    source_type: str               # picamera2 | opencv
    source: int | str
    width: int
    height: int
    fps: int
    jpeg_quality: int
    exposure_us: int | None
    gain: float | None
    enabled: bool = True


@dataclass(frozen=True)
class StreamConfig:
    node_id: str
    jetson_frame_endpoint: str
    send_enabled: bool
    request_timeout_s: float
    extra_headers: dict[str, str]


@dataclass(frozen=True)
class DebugConfig:
    save_debug_frames: bool
    debug_dir: Path
    debug_every_n_frames: int
    preview_enabled: bool


@dataclass(frozen=True)
class RuntimeConfig:
    stream: StreamConfig
    debug: DebugConfig
    cameras: list[CameraConfig]


class CameraSource:
    def read(self) -> np.ndarray:
        raise NotImplementedError

    def close(self) -> None:
        return None


class OpenCvSource(CameraSource):
    def __init__(self, config: CameraConfig) -> None:
        self.config = config
        source = int(config.source) if str(config.source).isdigit() else config.source
        self.capture = cv2.VideoCapture(source)
        if not self.capture.isOpened():
            raise RuntimeError(f"Could not open OpenCV source {config.source!r} for {config.camera_id}")
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.width)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.height)
        self.capture.set(cv2.CAP_PROP_FPS, config.fps)

    def read(self) -> np.ndarray:
        ok, frame = self.capture.read()
        if not ok or frame is None:
            raise RuntimeError(f"Could not read frame from {self.config.camera_id}")
        return cv2.resize(frame, (self.config.width, self.config.height), interpolation=cv2.INTER_AREA)

    def close(self) -> None:
        self.capture.release()


class Picamera2Source(CameraSource):
    def __init__(self, config: CameraConfig) -> None:
        self.config = config
        try:
            from picamera2 import Picamera2
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Picamera2 is required for source_type='picamera2'.") from exc

        camera_num = int(config.source)
        self.camera = Picamera2(camera_num=camera_num)
        video_cfg = self.camera.create_video_configuration(
            main={"size": (config.width, config.height), "format": "RGB888"},
            controls={"FrameRate": config.fps},
        )
        self.camera.configure(video_cfg)

        controls: dict[str, Any] = {}
        if config.exposure_us is not None:
            controls["ExposureTime"] = int(config.exposure_us)
        if config.gain is not None:
            controls["AnalogueGain"] = float(config.gain)
        if controls:
            self.camera.set_controls(controls)

        self.camera.start()
        time.sleep(0.35)

    def read(self) -> np.ndarray:
        frame_rgb = self.camera.capture_array()
        return cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

    def close(self) -> None:
        self.camera.stop()


class FramePublisher:
    def __init__(self, stream: StreamConfig, debug: DebugConfig) -> None:
        self.stream = stream
        self.debug = debug
        if stream.send_enabled and requests is None:
            raise RuntimeError("The requests package is required when send_enabled=true.")
        self._session = requests.Session() if requests is not None else None
        if self._session is not None and stream.extra_headers:
            self._session.headers.update(stream.extra_headers)

    def publish(
        self,
        camera: CameraConfig,
        node_id: str,
        frame_id: int,
        timestamp_ns: int,
        frame: np.ndarray,
    ) -> None:
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), camera.jpeg_quality])
        if not ok:
            raise RuntimeError(f"JPEG encode failed for {camera.camera_id}")
        payload = encoded.tobytes()

        if self.debug.save_debug_frames and frame_id % self.debug.debug_every_n_frames == 0:
            self.debug.debug_dir.mkdir(parents=True, exist_ok=True)
            out = self.debug.debug_dir / f"{node_id}_{camera.camera_id}_{frame_id:07d}.jpg"
            out.write_bytes(payload)

        if not self.stream.send_enabled:
            return
        if self._session is None:
            raise RuntimeError("HTTP session is unavailable.")

        response = self._session.post(
            self.stream.jetson_frame_endpoint,
            data={
                "node_id": node_id,
                "camera_id": camera.camera_id,
                "camera_name": camera.name,
                "frame_id": str(frame_id),
                "timestamp_ns": str(timestamp_ns),
                "width": str(camera.width),
                "height": str(camera.height),
                "format": "jpeg",
            },
            files={
                "frame": (
                    f"{node_id}_{camera.camera_id}_{frame_id:07d}.jpg",
                    payload,
                    "image/jpeg",
                )
            },
            timeout=self.stream.request_timeout_s,
        )
        response.raise_for_status()


def _int(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _source_from_config(camera_cfg: CameraConfig) -> CameraSource:
    source_type = camera_cfg.source_type.strip().lower()
    if source_type == "picamera2":
        return Picamera2Source(camera_cfg)
    if source_type == "opencv":
        return OpenCvSource(camera_cfg)
    raise ValueError(f"Unsupported source_type={camera_cfg.source_type!r} for {camera_cfg.camera_id}")


def load_config(path: Path) -> RuntimeConfig:
    with path.open("r", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream) or {}

    capture_defaults = raw.get("capture_defaults", {})
    raw_device = raw.get("device", {})
    raw_streaming = raw.get("streaming", {})
    raw_debug = raw.get("debug", {})

    cameras: list[CameraConfig] = []
    for item in raw.get("cameras", []):
        controls = item.get("controls", {})
        cameras.append(
            CameraConfig(
                camera_id=str(item["id"]),
                name=str(item.get("name", item["id"])),
                source_type=str(item.get("source_type", "picamera2")),
                source=item.get("source", 0),
                width=_int(item.get("width"), _int(capture_defaults.get("width"), 1280)),
                height=_int(item.get("height"), _int(capture_defaults.get("height"), 720)),
                fps=_int(item.get("fps"), _int(capture_defaults.get("fps"), 30)),
                jpeg_quality=_int(item.get("jpeg_quality"), _int(capture_defaults.get("jpeg_quality"), 85)),
                exposure_us=None if controls.get("exposure_us") is None else int(controls.get("exposure_us")),
                gain=_float_or_none(controls.get("gain")),
                enabled=bool(item.get("enabled", True)),
            )
        )

    enabled_cameras = [cfg for cfg in cameras if cfg.enabled]
    if not enabled_cameras:
        raise ValueError("No enabled cameras found in camera_config.yaml")

    stream_config = StreamConfig(
        node_id=str(raw_device.get("node_id", "pi_a")),
        jetson_frame_endpoint=str(raw_streaming.get("jetson_frame_endpoint", "http://127.0.0.1:8000/ingest/frame")),
        send_enabled=bool(raw_streaming.get("send_enabled", True)),
        request_timeout_s=float(raw_streaming.get("request_timeout_s", 1.0)),
        extra_headers={str(k): str(v) for k, v in (raw_streaming.get("headers") or {}).items()},
    )
    debug_config = DebugConfig(
        save_debug_frames=bool(raw_debug.get("save_debug_frames", False)),
        debug_dir=Path(str(raw_debug.get("debug_dir", "debug_frames"))),
        debug_every_n_frames=max(1, _int(raw_debug.get("debug_every_n_frames"), 60)),
        preview_enabled=bool(raw_debug.get("preview_enabled", False)),
    )
    return RuntimeConfig(stream=stream_config, debug=debug_config, cameras=enabled_cameras)


def _run_camera_loop(
    runtime: RuntimeConfig,
    camera_cfg: CameraConfig,
    stop_event: threading.Event,
) -> None:
    source = _source_from_config(camera_cfg)
    publisher = FramePublisher(runtime.stream, runtime.debug)

    frame_id = 0
    period_s = 1.0 / max(camera_cfg.fps, 1)
    next_tick = time.perf_counter()
    last_log = time.perf_counter()

    log.info(
        "Camera %s started source=%s target=%dx%d@%d",
        camera_cfg.camera_id,
        camera_cfg.source_type,
        camera_cfg.width,
        camera_cfg.height,
        camera_cfg.fps,
    )

    try:
        while not stop_event.is_set():
            now = time.perf_counter()
            if now < next_tick:
                time.sleep(min(next_tick - now, 0.01))
                continue

            frame = source.read()
            frame_id += 1
            timestamp_ns = time.time_ns()

            try:
                publisher.publish(camera_cfg, runtime.stream.node_id, frame_id, timestamp_ns, frame)
            except Exception as exc:  # noqa: BLE001
                log.warning("Publish failed camera=%s frame=%d err=%s", camera_cfg.camera_id, frame_id, exc)

            if runtime.debug.preview_enabled:
                cv2.imshow(camera_cfg.camera_id, frame)
                cv2.waitKey(1)

            if time.perf_counter() - last_log > 5.0:
                log.info("Camera %s streamed %d frames", camera_cfg.camera_id, frame_id)
                last_log = time.perf_counter()

            next_tick += period_s
    finally:
        source.close()
        if runtime.debug.preview_enabled:
            cv2.destroyWindow(camera_cfg.camera_id)
        log.info("Camera %s stopped after %d frames", camera_cfg.camera_id, frame_id)


def run(runtime: RuntimeConfig) -> None:
    stop_event = threading.Event()

    def _signal_handler(signum: int, _frame: Any) -> None:
        log.info("Signal %s received, stopping camera streamer", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    workers = [
        threading.Thread(
            target=_run_camera_loop,
            args=(runtime, camera_cfg, stop_event),
            daemon=True,
            name=f"cam-{camera_cfg.camera_id}",
        )
        for camera_cfg in runtime.cameras
    ]
    for worker in workers:
        worker.start()

    while any(worker.is_alive() for worker in workers):
        time.sleep(0.2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stream Raspberry Pi camera frames to the Jetson ingest endpoint.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("camera_config.yaml"),
        help="Path to camera streamer YAML config.",
    )
    parser.add_argument("--send", action="store_true", help="Force network sending on.")
    parser.add_argument("--no-send", action="store_true", help="Force network sending off.")
    parser.add_argument("--print-config", action="store_true", help="Print resolved config and exit.")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s :: %(message)s",
    )
    args = parse_args()
    runtime = load_config(args.config)

    if args.send and args.no_send:
        raise SystemExit("Use either --send or --no-send, not both.")
    if args.send:
        runtime = RuntimeConfig(
            stream=StreamConfig(**{**asdict(runtime.stream), "send_enabled": True}),
            debug=runtime.debug,
            cameras=runtime.cameras,
        )
    if args.no_send:
        runtime = RuntimeConfig(
            stream=StreamConfig(**{**asdict(runtime.stream), "send_enabled": False}),
            debug=runtime.debug,
            cameras=runtime.cameras,
        )

    if args.print_config:
        print(json.dumps(asdict(runtime), default=str, indent=2))
        return

    log.info(
        "Starting camera streamer node=%s cameras=%d send=%s endpoint=%s",
        runtime.stream.node_id,
        len(runtime.cameras),
        runtime.stream.send_enabled,
        runtime.stream.jetson_frame_endpoint,
    )
    run(runtime)


if __name__ == "__main__":
    main()
