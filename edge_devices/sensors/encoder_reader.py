"""Quadrature encoder reader.

Reads GPIO encoder channels, tracks pulse count and cable position, and can
optionally publish snapshots to the Jetson API endpoint.
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
import time
from dataclasses import dataclass, asdict
from math import pi
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


log = logging.getLogger(__name__)


class _DummyGPIO:
    BOARD = "BOARD"
    IN = "IN"
    PUD_UP = "PUD_UP"

    def setmode(self, *_): pass
    def setup(self, *_, **__): pass
    def input(self, *_): return 1
    def cleanup(self): pass


def _load_gpio():
    try:
        import RPi.GPIO as GPIO  # type: ignore
        return GPIO
    except Exception:
        try:
            import Jetson.GPIO as GPIO  # type: ignore
            return GPIO
        except Exception:
            log.warning("No GPIO backend detected; encoder reader uses dummy GPIO")
            return _DummyGPIO()


@dataclass(frozen=True)
class EncoderConfig:
    pin_a: int = 29
    pin_b: int = 31
    pulses_per_rev: int = 2000
    wheel_diameter_mm: float = 100.0
    direction: str = "forward"              # forward | reverse
    poll_interval_ms: float = 0.5
    publish_endpoint: str = "http://127.0.0.1:8000/ingest/encoder"
    publish_enabled: bool = False
    publish_period_s: float = 0.2
    request_timeout_s: float = 0.7


@dataclass(frozen=True)
class EncoderSnapshot:
    timestamp: float
    pulses: int
    delta_pulses: int
    position_m: float
    speed_mps: float


class EncoderReader:
    _TRANSITIONS = {
        (0, 1): 1,
        (1, 3): 1,
        (3, 2): 1,
        (2, 0): 1,
        (1, 0): -1,
        (3, 1): -1,
        (2, 3): -1,
        (0, 2): -1,
    }

    def __init__(self, config: EncoderConfig | None = None):
        self.config = config or EncoderConfig()
        if self.config.pulses_per_rev <= 0:
            raise ValueError("pulses_per_rev must be > 0")

        self._gpio = _load_gpio()
        self._gpio.setmode(self._gpio.BOARD)
        self._gpio.setup(self.config.pin_a, self._gpio.IN, pull_up_down=self._gpio.PUD_UP)
        self._gpio.setup(self.config.pin_b, self._gpio.IN, pull_up_down=self._gpio.PUD_UP)

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sign = 1 if self.config.direction == "forward" else -1
        self._mm_per_pulse = (pi * self.config.wheel_diameter_mm) / self.config.pulses_per_rev
        self._pulses = 0
        self._last_state = self._read_state()
        self._last_snapshot_ts = time.time()
        self._last_snapshot_pulses = 0

        self._session = requests.Session() if requests is not None else None

    def _read_state(self) -> int:
        a = 1 if self._gpio.input(self.config.pin_a) else 0
        b = 1 if self._gpio.input(self.config.pin_b) else 0
        return (a << 1) | b

    def _poll_once(self) -> None:
        state = self._read_state()
        if state == self._last_state:
            return
        delta = self._TRANSITIONS.get((self._last_state, state), 0)
        if delta:
            with self._lock:
                self._pulses += delta * self._sign
        self._last_state = state

    def _make_snapshot(self) -> EncoderSnapshot:
        now = time.time()
        with self._lock:
            pulses = self._pulses
        delta = pulses - self._last_snapshot_pulses
        dt = max(now - self._last_snapshot_ts, 1e-9)

        self._last_snapshot_pulses = pulses
        self._last_snapshot_ts = now
        position_m = (pulses * self._mm_per_pulse) / 1000.0
        speed_mps = ((delta * self._mm_per_pulse) / 1000.0) / dt

        return EncoderSnapshot(
            timestamp=now,
            pulses=pulses,
            delta_pulses=delta,
            position_m=position_m,
            speed_mps=speed_mps,
        )

    def _publish_snapshot(self, snapshot: EncoderSnapshot) -> None:
        if not self.config.publish_enabled:
            return
        if self._session is None:
            raise RuntimeError("requests is required for publish_enabled=true")
        response = self._session.post(
            self.config.publish_endpoint,
            json=asdict(snapshot),
            timeout=self.config.request_timeout_s,
        )
        response.raise_for_status()

    def _run(self) -> None:
        poll_period = max(self.config.poll_interval_ms, 0.1) / 1000.0
        next_publish = time.perf_counter() + self.config.publish_period_s

        while not self._stop.is_set():
            self._poll_once()

            now = time.perf_counter()
            if now >= next_publish:
                snapshot = self._make_snapshot()
                try:
                    self._publish_snapshot(snapshot)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Encoder publish failed: %s", exc)
                next_publish = now + self.config.publish_period_s
            time.sleep(poll_period)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="encoder-reader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        try:
            self._gpio.cleanup()
        except Exception:
            pass

    def snapshot(self) -> EncoderSnapshot:
        return self._make_snapshot()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Quadrature encoder reader")
    parser.add_argument("--pin-a", type=int, default=29)
    parser.add_argument("--pin-b", type=int, default=31)
    parser.add_argument("--pulses-per-rev", type=int, default=2000)
    parser.add_argument("--wheel-diameter-mm", type=float, default=100.0)
    parser.add_argument("--direction", choices=("forward", "reverse"), default="forward")
    parser.add_argument("--poll-interval-ms", type=float, default=0.5)
    parser.add_argument("--publish-endpoint", default="http://127.0.0.1:8000/ingest/encoder")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--publish-period-s", type=float, default=0.2)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s :: %(message)s",
    )
    args = parse_args()
    reader = EncoderReader(
        EncoderConfig(
            pin_a=args.pin_a,
            pin_b=args.pin_b,
            pulses_per_rev=args.pulses_per_rev,
            wheel_diameter_mm=args.wheel_diameter_mm,
            direction=args.direction,
            poll_interval_ms=args.poll_interval_ms,
            publish_endpoint=args.publish_endpoint,
            publish_enabled=args.publish,
            publish_period_s=args.publish_period_s,
        )
    )

    stop_event = threading.Event()

    def _signal_handler(signum: int, _frame: Any) -> None:
        log.info("Signal %s received, stopping encoder reader", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    reader.start()
    while not stop_event.is_set():
        snap = reader.snapshot()
        log.info(
            "encoder pulses=%d position=%.4fm speed=%.4fm/s",
            snap.pulses,
            snap.position_m,
            snap.speed_mps,
        )
        time.sleep(1.0)
    reader.stop()
    print(json.dumps(asdict(reader.snapshot()), default=str))


if __name__ == "__main__":
    main()

