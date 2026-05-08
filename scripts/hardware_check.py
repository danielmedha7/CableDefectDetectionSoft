"""Hardware bring-up diagnostic.

Run on the Jetson before the first full pipeline run. Each check is
independent and prints PASS / FAIL / SKIP. The script never raises —
collect every result, then summarise.

    python scripts/hardware_check.py
    python scripts/hardware_check.py --skip yolo,ultrasonic
    python scripts/hardware_check.py --quick   # skip warmup-heavy checks
"""
from __future__ import annotations

import argparse
import importlib
import logging
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s :: %(message)s")
log = logging.getLogger("hwcheck")


# ─── helpers ───
RESULTS: list[tuple[str, str, str]] = []   # (name, status, detail)


def record(name: str, status: str, detail: str = "") -> None:
    RESULTS.append((name, status, detail))
    icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "·", "WARN": "!"}.get(status, "?")
    print(f"  {icon} {status:4} {name}   {detail}")


def section(title: str) -> None:
    print(f"\n── {title} " + "─" * (60 - len(title)))


# ─── checks ───
def check_python() -> None:
    section("Python")
    v = sys.version_info
    msg = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 10):
        record("python_version", "PASS", msg)
    elif v >= (3, 8):
        record("python_version", "WARN",
               f"{msg} — works, but pipeline uses 3.10 syntax in places. "
               "Install 3.10+ if you hit `unsupported operand type` errors.")
    else:
        record("python_version", "FAIL",
               f"{msg} — need 3.8+. Install miniforge / pyenv.")


def check_imports() -> None:
    section("Python packages")
    needed = ["numpy", "yaml", "cv2", "fastapi", "sqlalchemy", "pydantic", "zmq"]
    optional = ["ultralytics", "xgboost", "uvicorn", "aiosqlite"]
    pi_only = ["picamera2", "gpiozero"]
    jetson_only = ["Jetson.GPIO"]

    for mod in needed:
        try:
            importlib.import_module(mod)
            record(f"import {mod}", "PASS")
        except Exception as e:
            record(f"import {mod}", "FAIL", str(e))
    for mod in optional:
        try:
            importlib.import_module(mod)
            record(f"import {mod}", "PASS")
        except Exception:
            record(f"import {mod}", "SKIP", "optional — install if you need this feature")
    for mod in jetson_only:
        try:
            importlib.import_module(mod)
            record(f"import {mod}", "PASS", "(Jetson detected)")
        except Exception:
            record(f"import {mod}", "SKIP", "Jetson-only")
    for mod in pi_only:
        try:
            importlib.import_module(mod)
            record(f"import {mod}", "PASS", "(Pi detected)")
        except Exception:
            record(f"import {mod}", "SKIP", "Pi-only")


def check_config() -> None:
    section("Config files")
    for name in ("config.yaml", "qc_rules.yaml", "calibration.yaml"):
        p = ROOT / "configs" / name
        if not p.exists():
            record(f"configs/{name}", "FAIL", "missing")
            continue
        try:
            with p.open("r", encoding="utf-8") as f:
                yaml.safe_load(f)
            record(f"configs/{name}", "PASS")
        except Exception as e:
            record(f"configs/{name}", "FAIL", f"YAML parse: {e}")


def check_cameras(skip: bool) -> None:
    section("Cameras (4× IMX219 via Arducam HAT)")
    if skip:
        record("camera open ×4", "SKIP")
        return
    try:
        import cv2
    except ImportError:
        record("camera open ×4", "FAIL", "OpenCV missing")
        return
    cfg_path = ROOT / "configs" / "config.yaml"
    cfg = yaml.safe_load(cfg_path.open()) if cfg_path.exists() else {}
    cam_cfg = cfg.get("cameras", {})
    sensor_ids = cam_cfg.get("sensor_ids", [0, 1, 2, 3])
    w, h = cam_cfg.get("resolution", [1920, 1080])
    fps = cam_cfg.get("fps", 30)

    for sid in sensor_ids:
        pipe = (
            f"nvarguscamerasrc sensor-id={sid} ! "
            f"video/x-raw(memory:NVMM),width={w},height={h},framerate={fps}/1,format=NV12 ! "
            f"nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! "
            f"video/x-raw,format=BGR ! appsink drop=1"
        )
        try:
            cap = cv2.VideoCapture(pipe, cv2.CAP_GSTREAMER)
            ok = cap.isOpened()
            if ok:
                ok2, _ = cap.read()
                cap.release()
                record(f"camera sensor-id={sid}", "PASS" if ok2 else "FAIL",
                       "open ok, no frame" if not ok2 else "")
            else:
                record(f"camera sensor-id={sid}", "FAIL",
                       "VideoCapture not opened — check Arducam HAT, i2cdetect 6")
        except Exception as e:
            record(f"camera sensor-id={sid}", "FAIL", str(e))


def check_gpio() -> None:
    section("GPIO (Jetson)")
    try:
        import Jetson.GPIO as GPIO  # type: ignore
    except Exception:
        record("Jetson.GPIO", "SKIP", "not on Jetson")
        return
    try:
        GPIO.setmode(GPIO.BOARD)
        for pin in (11, 18, 29, 31):
            try:
                GPIO.setup(pin, GPIO.OUT if pin in (11, 18) else GPIO.IN)
                record(f"gpio pin {pin}", "PASS")
            except Exception as e:
                record(f"gpio pin {pin}", "FAIL", str(e))
        GPIO.cleanup()
    except Exception as e:
        record("gpio init", "FAIL", str(e))


def check_i2c() -> None:
    section("I2C bus 6 (Arducam HAT)")
    try:
        import subprocess
        out = subprocess.run(
            ["i2cdetect", "-y", "-r", "6"],
            capture_output=True, text=True, timeout=2.0,
        )
        if out.returncode == 0 and "24" in out.stdout:
            record("Arducam HAT @ 0x24", "PASS")
        elif out.returncode == 0:
            record("Arducam HAT @ 0x24", "FAIL",
                   "i2cdetect ran but device not found — check ribbon cable")
        else:
            record("i2cdetect", "FAIL", out.stderr.strip() or "non-zero exit")
    except FileNotFoundError:
        record("i2cdetect", "SKIP", "i2c-tools not installed")
    except Exception as e:
        record("i2cdetect", "SKIP", str(e))


def check_yolo(skip: bool) -> None:
    section("YOLO model")
    if skip:
        record("yolo engine load", "SKIP")
        return
    cfg = yaml.safe_load((ROOT / "configs" / "config.yaml").open())
    engine_path = ROOT / cfg["inference"]["yolo_engine"]
    if not engine_path.exists():
        record("yolo engine file", "FAIL", f"missing: {engine_path}")
        return
    record("yolo engine file", "PASS", str(engine_path))
    try:
        from ai_models import YOLODetector
        t0 = time.time()
        d = YOLODetector(engine_path=str(engine_path), warmup_runs=1)
        record("yolo load + warmup", "PASS", f"{(time.time()-t0):.1f}s")
        del d
    except Exception as e:
        record("yolo load + warmup", "FAIL", str(e))


def check_xgboost(skip: bool) -> None:
    section("XGBoost models")
    if skip:
        record("xgboost models", "SKIP")
        return
    cfg = yaml.safe_load((ROOT / "configs" / "config.yaml").open())
    paths = cfg["inference"].get("xgboost", {})
    for key in ("classifier", "depth", "size"):
        p = ROOT / paths.get(key, "")
        if p.exists():
            record(f"xgb_{key}", "PASS")
        else:
            record(f"xgb_{key}", "FAIL", f"missing: {p}")


def check_disk_space() -> None:
    section("Disk")
    try:
        import shutil
        free = shutil.disk_usage(str(ROOT)).free / (1024 ** 3)
        if free > 5:
            record("free space", "PASS", f"{free:.1f} GB")
        elif free > 1:
            record("free space", "WARN", f"{free:.1f} GB")
        else:
            record("free space", "FAIL", f"{free:.1f} GB — too low for image crops + logs")
    except Exception as e:
        record("free space", "SKIP", str(e))


# ─── main ───
def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--skip", default="", help="comma list: yolo,xgboost,cameras")
    p.add_argument("--quick", action="store_true", help="skip warmup-heavy checks")
    args = p.parse_args()

    skip = set(s.strip() for s in args.skip.split(",") if s.strip())
    if args.quick:
        skip.update({"yolo", "xgboost"})

    print("CableVision OS — hardware diagnostic")
    print("=====================================")

    check_python()
    check_imports()
    check_config()
    check_cameras("cameras" in skip)
    check_gpio()
    check_i2c()
    check_yolo("yolo" in skip)
    check_xgboost("xgboost" in skip)
    check_disk_space()

    # summary
    print("\n── Summary " + "─" * 53)
    by_status: dict[str, int] = {}
    for _, status, _ in RESULTS:
        by_status[status] = by_status.get(status, 0) + 1
    for s in ("PASS", "WARN", "SKIP", "FAIL"):
        if s in by_status:
            print(f"  {s}: {by_status[s]}")

    fails = by_status.get("FAIL", 0)
    if fails:
        print(f"\n  {fails} hard failure(s) — fix before running the full pipeline.")
        return 1
    print("\n  All hard checks passed. You can run the pipeline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
