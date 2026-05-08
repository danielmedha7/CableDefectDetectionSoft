"""GPIO LED and laser controller.

Provides deterministic pulse control used during frame capture and alignment.
Pin numbering follows BOARD mode to stay consistent with configs/config.yaml.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass


log = logging.getLogger(__name__)


class _DummyGPIO:
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
        import RPi.GPIO as GPIO  # type: ignore
        return GPIO
    except Exception:
        try:
            import Jetson.GPIO as GPIO  # type: ignore
            return GPIO
        except Exception:
            log.warning("No GPIO backend detected; LED controller uses dummy GPIO")
            return _DummyGPIO()


@dataclass(frozen=True)
class LedControllerConfig:
    led_pin: int = 18
    laser_pin: int = 11
    pulse_us: int = 800
    board_mode: str = "BOARD"


class LEDController:
    """Controls LED and laser GPIO pins with optional microsecond pulses."""

    def __init__(self, config: LedControllerConfig | None = None):
        self.config = config or LedControllerConfig()
        self._gpio = _load_gpio()
        self._initialise_gpio()

    def _initialise_gpio(self) -> None:
        self._gpio.setmode(getattr(self._gpio, self.config.board_mode, self._gpio.BOARD))
        self._gpio.setup(self.config.led_pin, self._gpio.OUT, initial=self._gpio.LOW)
        self._gpio.setup(self.config.laser_pin, self._gpio.OUT, initial=self._gpio.LOW)

    def set_led(self, on: bool) -> None:
        self._gpio.output(self.config.led_pin, self._gpio.HIGH if on else self._gpio.LOW)

    def set_laser(self, on: bool) -> None:
        self._gpio.output(self.config.laser_pin, self._gpio.HIGH if on else self._gpio.LOW)

    def pulse(self, led: bool = True, laser: bool = False, pulse_us: int | None = None) -> None:
        """Pulse LED and/or laser for the configured microsecond duration."""
        width_us = self.config.pulse_us if pulse_us is None else max(1, int(pulse_us))
        if led:
            self.set_led(True)
        if laser:
            self.set_laser(True)
        time.sleep(width_us / 1_000_000.0)
        if led:
            self.set_led(False)
        if laser:
            self.set_laser(False)

    def close(self) -> None:
        self.set_led(False)
        self.set_laser(False)
        try:
            self._gpio.cleanup()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        self.close()

