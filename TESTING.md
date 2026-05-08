# CableVision OS — Testing Guide

This guide walks through testing the project at four levels:

1. **Unit / smoke tests** (laptop)
2. **Dry-run pipeline** (laptop, no hardware)
3. **Hardware-in-the-loop** (Jetson + Pi + sensors)
4. **End-to-end with dashboard** (Jetson + LAN PC)

---

## 0. One-time setup

### Backend / Python
```bash
cd CableDefectDetectionSoft
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux:    source .venv/bin/activate
pip install -r requirements.txt
```

### Dashboard
```bash
cd dashboard
npm install
```

> Note: `Jetson.GPIO`, `gpiozero`, `picamera2`, `spidev`, `RPi.GPIO` are intentionally
> commented out in `requirements.txt`. They are **only** needed on the actual hardware.
> Every module in this project gracefully falls back when these are absent so you can
> develop on Windows / macOS / plain Linux.

---

## 1. Unit / smoke tests (laptop, no hardware)

```bash
python -m pytest tests/ -v
```

Expected output:
```
tests/test_smoke.py::test_multiview_fusion_dedups_across_cameras PASSED
tests/test_smoke.py::test_severity_classifier_levels             PASSED
tests/test_smoke.py::test_position_tracker_pulses_to_metres      PASSED
tests/test_smoke.py::test_qc_engine_fail_on_forbidden_class      PASSED
tests/test_smoke.py::test_session_manager_lifecycle              PASSED
tests/test_smoke.py::test_ultrasonic_feature_vector_length       PASSED
tests/test_smoke.py::test_diameter_estimator_runs_without_frames PASSED
tests/test_smoke.py::test_roundness_monitor_basic                PASSED
tests/test_smoke.py::test_report_generator_writes_files          PASSED
```

If any test fails, fix that module before moving on — they cover every algorithmic
piece (fusion, severity, QC, position, ultrasonic features, diameter, roundness, reports).

---

## 2. Module-by-module quick checks

### 2.1 QC engine — load rules and evaluate one defect
```bash
python -c "
from ai_models.multiview_fusion import WorldDefect
from system_logic.qc_engine import QCEngine
qc = QCEngine('configs/qc_rules.yaml', 'HV_35mm')
d = WorldDefect(cls='crack', confidence=0.9, angle_deg=12, position_m=3.0,
                bbox=(0,0,10,10), camera_id=1, depth_mm=1.2, severity='high')
print(qc.evaluate_defect(d, cable_length_m=5.0))
"
```

### 2.2 Ultrasonic features on synthetic signal
```bash
python -c "
import numpy as np
from processing.ultrasonic_processing.feature_extractor import UltrasonicFeatureExtractor
fx = UltrasonicFeatureExtractor()
sig = np.sin(np.linspace(0, 50, 2000)) * np.exp(-np.linspace(0,5,2000))
print('feature vector:', fx.extract(sig).shape)
"
```

### 2.3 Synthetic ultrasonic acquirer (no SPI needed)
```bash
python -c "
import time
from edge_devices.sensors.ultrasonic_acquirer import UltrasonicAcquirer
ua = UltrasonicAcquirer()
ua.start()
time.sleep(0.3)
print({ch: sig.shape for ch, sig in ua.latest_all().items()})
ua.stop()
"
```

### 2.4 Encoder reader in synthetic mode
```bash
python -c "
import time
from edge_devices.sensors.encoder_reader import EncoderReader
e = EncoderReader(synthetic_rate_pps=500); e.start()
time.sleep(0.2)
print('backend=', e.backend, 'pulses=', e.pulses); e.stop()
"
```

---

## 3. Dry-run the pipeline (laptop)

The orchestrator can run end-to-end *without* cameras / TensorRT / XGBoost models.
It will log "no live capture" and idle, but still loads configs, opens the QC engine,
starts the session, and writes a report on Ctrl-C.

> **Skip YOLO / XGB load:** edit `configs/config.yaml` and either:
> - point `inference.yolo_engine` and `inference.xgboost.*` paths to dummy files, or
> - comment out the `YOLODetector(...)` line in `main_pipeline.py` if you don't have
>   `.engine` / `.ubj` files yet.

```bash
python main_pipeline.py --cable C-001 --spec HV_35mm --dry-run
# Ctrl-C to stop; check ./data/reports/<session>_C-001.html
```

---

## 4. Hardware-in-the-loop (Jetson + Pi + sensors)

### 4.1 Calibrate the laser baseline (one-time)
Mount the laser, point at a known-flat reference at the working distance:
```bash
python -c "
import cv2
from processing.image_processing.laser_triangulation import LaserTriangulator
lt = LaserTriangulator()
caps = [cv2.VideoCapture(0).read()[1] for _ in range(20)]
lt.calibrate_baseline(caps, 'configs/laser_baseline.npy')
"
```

### 4.2 Verify each sensor independently
- **Cameras (Jetson):** `gst-launch-1.0 nvarguscamerasrc sensor-id=0 ! nvvidconv ! nveglglessink`
- **LED:** `python -c "from edge_devices.sensors.led_controller import LEDController; l=LEDController(); l.continuous(0.6); input('Press Enter…'); l.off()"`
- **Encoder:** roll the wheel by hand and watch the pulse count grow.
- **Ultrasonic:** confirm A-scan window is non-zero on a real test pulse.

### 4.3 Run the streamers on each Pi
```bash
# on each Raspberry Pi
python edge_devices/raspberry_pi/camera_streamer.py \
    --config edge_devices/raspberry_pi/camera_config.yaml \
    --cam-id 0   # 0,1,2,3 per Pi
```

### 4.4 Run the full pipeline on the Jetson
```bash
python main_pipeline.py --cable C-101 --spec HV_35mm
```

---

## 5. End-to-end with dashboard

### 5.1 Start the backend (Jetson)
```bash
# After backend_api/ is implemented:
uvicorn backend_api.main:app --host 0.0.0.0 --port 8000
```

### 5.2 Start the dashboard (LAN PC)
```bash
cd dashboard
# point at your Jetson:
echo "VITE_API_BASE=http://192.168.1.10:8000"  > .env.local
echo "VITE_WS_URL=ws://192.168.1.10:8000/ws"  >> .env.local
npm run dev    # opens http://localhost:3000
```

### 5.3 Smoke-test the UI without backend
The dashboard renders cleanly even if the backend is down — every panel
shows "OFFLINE" / empty states. To test pages individually:

```bash
# Add this to a router in main.jsx (or create one):
#   /          → LiveMonitor
#   /profile   → CableProfile
#   /internal  → InternalDefectLog
#   /analytics → Analytics
#   /reports   → Reports
```

---

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ImportError: ultralytics` | YOLO not installed | `pip install ultralytics` (skip on dev box) |
| `FileNotFoundError: yolov8.engine` | TensorRT model not exported | export from training pipeline, place in `models/` |
| `Jetson.GPIO not available` warning | Running off-Jetson | Expected — dummy stub kicks in |
| `gpiozero unavailable` | Running off-Pi | Expected — encoder runs synthetic, LED stubbed |
| WebSocket stuck "OFFLINE" in dashboard | Backend not running or wrong URL | Check `VITE_WS_URL`, confirm `:8000/ws` reachable |
| Dashboard build fails on Tailwind | First-time install missing config | `npx tailwindcss init -p` then add the standard `@tailwind` directives |
| Reports empty | No defects added before `session.end()` | Confirm fusion / detector are emitting |

---

## 7. What "passing" means at each layer

| Layer | Passing criterion |
|---|---|
| Unit tests | `pytest` shows 9/9 PASS |
| Dry-run | Session report HTML renders; QC verdict written |
| Hardware bring-up | All 4 cameras stream; encoder counts; ultrasonic shows live waveform |
| Full system | Defect injected at known position appears in dashboard within 1 s |
