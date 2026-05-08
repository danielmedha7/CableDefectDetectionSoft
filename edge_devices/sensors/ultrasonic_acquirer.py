"""Ultrasonic A-scan acquirer.

Reads raw ADC windows over SPI from multi-channel ultrasonic front-end hardware
and optionally posts windows to the Jetson ingest endpoint.
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

import numpy as np

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UltrasonicConfig:
    sample_rate_hz: int = 1_000_000
    pulse_freq_hz: int = 500_000
    channels: int = 4
    samples_per_window: int = 1024
    gate_start_us: float = 10.0
    gate_end_us: float = 120.0
    spi_device: str = "/dev/spidev0.0"
    spi_max_speed_hz: int = 8_000_000
    spi_mode: int = 0
    publish_endpoint: str = "http://127.0.0.1:8000/ingest/ultrasonic"
    publish_enabled: bool = False
    request_timeout_s: float = 1.0


class UltrasonicAcquirer:
    """Acquires multi-channel ultrasonic windows from SPI ADC hardware."""

    def __init__(self, config: UltrasonicConfig | None = None):
        self.config = config or UltrasonicConfig()
        self._session = requests.Session() if requests is not None else None
        self._spi = self._open_spi(self.config.spi_device)

    @staticmethod
    def _parse_spi_device(spi_device: str) -> tuple[int, int]:
        # format: /dev/spidev<bus>.<device>
        token = Path(spi_device).name
        if not token.startswith("spidev") or "." not in token:
            raise ValueError(f"Invalid spi_device: {spi_device}")
        bus_str, dev_str = token.replace("spidev", "").split(".", 1)
        return int(bus_str), int(dev_str)

    def _open_spi(self, spi_device: str):
        try:
            import spidev  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("spidev is required for ultrasonic acquisition.") from exc

        bus, device = self._parse_spi_device(spi_device)
        spi = spidev.SpiDev()
        spi.open(bus, device)
        spi.max_speed_hz = int(self.config.spi_max_speed_hz)
        spi.mode = int(self.config.spi_mode)
        return spi

    @staticmethod
    def _decode_adc12(raw: list[int]) -> int:
        if len(raw) < 3:
            return 0
        return ((raw[1] & 0x0F) << 8) | raw[2]

    def _read_adc_channel(self, channel: int) -> int:
        # MCP3xxx-style read command (works for common 10/12-bit SPI ADCs).
        cmd = [0x01, 0x80 | ((channel & 0x07) << 4), 0x00]
        raw = self._spi.xfer2(cmd)
        return self._decode_adc12(raw)

    def acquire_window(self, channel: int) -> np.ndarray:
        sample_count = int(self.config.samples_per_window)
        out = np.empty(sample_count, dtype=np.float32)
        period_s = 1.0 / max(self.config.sample_rate_hz, 1)
        next_tick = time.perf_counter()

        for idx in range(sample_count):
            val = self._read_adc_channel(channel)
            out[idx] = float(val)
            next_tick += period_s
            while True:
                now = time.perf_counter()
                if now >= next_tick:
                    break
        return out

    def acquire_multichannel(self) -> dict[int, np.ndarray]:
        return {ch: self.acquire_window(ch) for ch in range(self.config.channels)}

    def publish(self, windows: dict[int, np.ndarray], timestamp: float | None = None) -> None:
        if not self.config.publish_enabled:
            return
        if self._session is None:
            raise RuntimeError("requests is required for publish_enabled=true")
        if timestamp is None:
            timestamp = time.time()

        payload = {
            "timestamp": timestamp,
            "sample_rate_hz": self.config.sample_rate_hz,
            "gate_start_us": self.config.gate_start_us,
            "gate_end_us": self.config.gate_end_us,
            "channels": {str(ch): sig.tolist() for ch, sig in windows.items()},
        }
        response = self._session.post(
            self.config.publish_endpoint,
            json=payload,
            timeout=self.config.request_timeout_s,
        )
        response.raise_for_status()

    def close(self) -> None:
        if self._spi is not None:
            try:
                self._spi.close()
            except Exception:
                pass
            self._spi = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        self.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ultrasonic A-scan acquirer")
    parser.add_argument("--sample-rate-hz", type=int, default=1_000_000)
    parser.add_argument("--pulse-freq-hz", type=int, default=500_000)
    parser.add_argument("--channels", type=int, default=4)
    parser.add_argument("--samples-per-window", type=int, default=1024)
    parser.add_argument("--gate-start-us", type=float, default=10.0)
    parser.add_argument("--gate-end-us", type=float, default=120.0)
    parser.add_argument("--spi-device", default="/dev/spidev0.0")
    parser.add_argument("--spi-max-speed-hz", type=int, default=8_000_000)
    parser.add_argument("--publish-endpoint", default="http://127.0.0.1:8000/ingest/ultrasonic")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--period-s", type=float, default=0.3)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s :: %(message)s",
    )
    args = parse_args()
    config = UltrasonicConfig(
        sample_rate_hz=args.sample_rate_hz,
        pulse_freq_hz=args.pulse_freq_hz,
        channels=args.channels,
        samples_per_window=args.samples_per_window,
        gate_start_us=args.gate_start_us,
        gate_end_us=args.gate_end_us,
        spi_device=args.spi_device,
        spi_max_speed_hz=args.spi_max_speed_hz,
        publish_endpoint=args.publish_endpoint,
        publish_enabled=args.publish,
    )

    stop_event = threading.Event()

    def _signal_handler(signum: int, _frame) -> None:
        log.info("Signal %s received, stopping ultrasonic acquirer", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    with UltrasonicAcquirer(config) as acquirer:
        if not args.continuous:
            windows = acquirer.acquire_multichannel()
            acquirer.publish(windows)
            payload = {str(ch): signal.shape[0] for ch, signal in windows.items()}
            print(json.dumps({"window_sizes": payload}))
            return

        while not stop_event.is_set():
            ts = time.time()
            windows = acquirer.acquire_multichannel()
            try:
                acquirer.publish(windows, timestamp=ts)
            except Exception as exc:  # noqa: BLE001
                log.warning("Ultrasonic publish failed: %s", exc)
            log.info("Acquired ultrasonic window channels=%d samples=%d", len(windows), config.samples_per_window)
            time.sleep(max(args.period_s, 0.0))


if __name__ == "__main__":
    main()

