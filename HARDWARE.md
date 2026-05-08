# CableVision OS — Hardware Bring-Up Guide

This is the on-the-bench checklist for going from a fresh Jetson + Pi cluster
to a running pipeline. Each step has a verification command — run it and
confirm the expected output before moving to the next.

---

## 0. Bill of materials (assumed)

| Role | Hardware |
|---|---|
| Inference + DB + WS bridge | NVIDIA Jetson Nano (or Orin Nano) |
| Camera nodes (×N) | Raspberry Pi 4/5 with PiCamera2 modules |
| Optical sensors | 4× IMX219 cameras + Arducam Multi-Camera HAT |
| Depth | Green line laser (~520 nm), mounted at ~30° to surface |
| Position | Quadrature rotary encoder (2000 PPR, 100 mm wheel) |
| Internal defect | 500 kHz ultrasonic transducer + SPI ADC |
| Lighting | Strobed LED ring (PWM-capable) |
| Dashboard host | Any LAN PC (Windows/macOS/Linux) |

---

## 1. Software baseline on the Jetson

### Python ≥ 3.10 (required)

The default Jetpack 4.6 ships Python 3.6.9 — **do not** use that. Install
miniforge:

```bash
wget https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-aarch64.sh
bash Miniforge3-Linux-aarch64.sh
conda create -n cablevision python=3.10 -y
conda activate cablevision
```

### Project deps

```bash
cd ~/CableDefectDetectionSoft
pip install -r requirements.txt
# Jetson-only:
pip install Jetson.GPIO
```

### Verify

```bash
python scripts/hardware_check.py
```

Expected: every check is `PASS` or `SKIP`. Any `FAIL` must be fixed.

---

## 2. Cameras (4× IMX219 via Arducam HAT)

### 2.1 I2C topology

The Arducam HAT lives on i2cbus 6 at address `0x24`. From a fresh boot:

```bash
sudo apt install -y i2c-tools
i2cdetect -y -r 6
# expected: 24 visible in the grid
```

### 2.2 Per-camera GStreamer probe

```bash
gst-launch-1.0 -v nvarguscamerasrc sensor-id=0 num-buffers=10 ! \
    'video/x-raw(memory:NVMM),width=1920,height=1080,framerate=30/1' ! \
    nvvidconv ! videoconvert ! fakesink
# repeat for sensor-id=1,2,3
```

Expected: `Setting pipeline to PAUSED → PLAYING → 10 buffers → EOS`. If a
camera fails: re-seat the ribbon cable, then re-run.

### 2.3 OpenCV pipe end-to-end

```bash
python -c "
import cv2
pipe = ('nvarguscamerasrc sensor-id=0 ! video/x-raw(memory:NVMM),width=1920,height=1080,format=NV12,framerate=30/1 ! '
        'nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! video/x-raw,format=BGR ! appsink')
cap = cv2.VideoCapture(pipe, cv2.CAP_GSTREAMER)
print('opened:', cap.isOpened(), 'frame ok:', cap.read()[0])
cap.release()
"
```

---

## 3. Encoder (rotary) on Jetson GPIO

Wiring (Jetson 40-pin header, BOARD numbering):

| Encoder | Jetson pin | BCM |
|---|---|---|
| A | 29 | 5 |
| B | 31 | 6 |
| GND | 6 | — |
| 3V3 | 1 | — |

Verify:

```bash
python -c "
import time
from edge_devices.sensors.encoder_reader import EncoderReader, EncoderConfig
e = EncoderReader(EncoderConfig(pin_a=29, pin_b=31, pulses_per_rev=2000, wheel_diameter_mm=100.0))
e.start()
print('Roll the wheel by hand for 5 s …')
time.sleep(5)
print('snapshot:', e.snapshot())
e.stop()
"
```

Expected: `pulses` increases as you roll the wheel. If pulses go negative
when rolling forward, set `direction: reverse` in `configs/config.yaml`.

---

## 4. LED + laser strobe (Jetson GPIO)

Wiring:

| Signal | Jetson pin | BCM | Notes |
|---|---|---|---|
| LED gate | 18 | 24 | drives MOSFET → LED ring |
| Laser gate | 11 | 17 | drives laser diode driver |

Verify:

```bash
python -c "
import time, Jetson.GPIO as GPIO
GPIO.setmode(GPIO.BOARD)
for p in (18, 11):
    GPIO.setup(p, GPIO.OUT, initial=GPIO.LOW)
print('LED on for 1s …'); GPIO.output(18, GPIO.HIGH); time.sleep(1); GPIO.output(18, GPIO.LOW)
print('Laser on for 1s …'); GPIO.output(11, GPIO.HIGH); time.sleep(1); GPIO.output(11, GPIO.LOW)
GPIO.cleanup()
"
```

Visually confirm the LED flashes for 1 s and the laser line appears.

---

## 5. Laser baseline calibration (one-time)

Place a perfectly flat reference (e.g. a machinist's surface plate) at the
nominal working distance. Then:

```bash
python -c "
import cv2, time, numpy as np
from processing.image_processing.laser_triangulation import LaserTriangulator, TriangulationConfig

pipe = ('nvarguscamerasrc sensor-id=0 ! video/x-raw(memory:NVMM),width=1920,height=1080,format=NV12,framerate=30/1 ! '
        'nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! video/x-raw,format=BGR ! appsink')
cap = cv2.VideoCapture(pipe, cv2.CAP_GSTREAMER)

# turn laser on first via GPIO (or do it manually)
frames = []
for _ in range(20):
    ok, f = cap.read()
    if ok: frames.append(f)
    time.sleep(0.1)
cap.release()

lt = LaserTriangulator(profile_width=1920, config=TriangulationConfig(working_distance_mm=250.0))
lt.calibrate_baseline(frames, save_to='configs/laser_baseline.npy')
print('Saved configs/laser_baseline.npy')
lt.close()
"
```

---

## 6. Ultrasonic acquirer

The transducer connects to an SPI ADC (MCP3xxx-style). Wiring goes to
`/dev/spidev0.0` by default. Test:

```bash
ls /dev/spidev*
# expected: /dev/spidev0.0

python -c "
from edge_devices.sensors.ultrasonic_acquirer import UltrasonicAcquirer, UltrasonicConfig
ua = UltrasonicAcquirer(UltrasonicConfig(channels=4, samples_per_window=512))
windows = ua.acquire_multichannel()
for ch, sig in windows.items():
    print(f'ch{ch}: shape={sig.shape}, peak={abs(sig).max():.1f}')
ua.close()
"
```

Expected: 4 channels, peak > 0 (real signal). All-zeros means SPI wiring or
ADC chip-select is wrong.

---

## 7. Raspberry Pi camera streamers

On each Pi:

```bash
# install deps
sudo apt update && sudo apt install -y python3-picamera2 python3-pip
pip install opencv-python pyyaml requests

# point at Jetson IP
nano edge_devices/raspberry_pi/camera_config.yaml
#   streaming.jetson_frame_endpoint: http://192.168.1.10:8000/ingest/frame

# run
python edge_devices/raspberry_pi/camera_streamer.py --send
```

Expected log on Pi: `Camera cam-0 streamed N frames` every 5 s.
Expected log on Jetson backend: `frame_meta` events broadcast.

---

## 8. Backend on Jetson

```bash
uvicorn backend_api.main:app --host 0.0.0.0 --port 8000
```

Verify from any LAN device:

```bash
curl http://<jetson-ip>:8000/health
# {"status":"healthy"}

curl http://<jetson-ip>:8000/dashboard/snapshot | python -m json.tool | head -20
```

---

## 9. Run the pipeline

In a separate terminal on the Jetson:

```bash
python main_pipeline.py --cable C-001 --spec HV_35mm
```

Expected log sequence:
```
Initialising YOLO detector …
YOLO warmup complete (5 runs)
Pipeline starting · cable=C-001 spec=HV_35mm
Session ALERT session_started …
Capture loop started
Inference loop started
encoder pump …
ALERT defect_detected …  (when a defect is in front of the cameras)
```

Press Ctrl-C to stop. A report is written to `data/reports/<session>_C-001.html`.

---

## 10. Dashboard on the LAN PC

```bash
cd dashboard_app
echo "VITE_API_BASE_URL=http://<jetson-ip>:8000" > .env.local
npm install
npm run dev    # opens http://localhost:5173
```

The dashboard polls `/dashboard/snapshot` every ~1.4 s. It will show:
- session id, status, line speed
- live diameter, roundness, scores
- 4 camera tiles (latest JPEG via `/api/cameras/{id}/snapshot`)
- recent defect rows

---

## 11. Wiring + GPIO summary card

Pin the table below near the bench.

```
JETSON (BOARD numbering)         RASPBERRY PI (BCM)
─────────────────────             ─────────────────
 11 → laser gate                   18 → LED strobe
 18 → LED gate                      5 → encoder A
 29 → encoder A                     6 → encoder B
 31 → encoder B                    17 → ultrasonic trigger
 i2cbus 6, addr 0x24 → Arducam     /dev/spidev0.0 → ultrasonic ADC
 /dev/spidev0.0     → ultrasonic
```

---

## 12. Known fragility / production gotchas

1. **YOLO TensorRT engine is GPU-architecture-specific.** Re-export the
   .engine on the same Jetson hardware (Maxwell=Nano, Ampere=Orin). A model
   from another machine may load but produce garbage.

2. **Power.** The Arducam HAT + 4 IMX219s + LED ring + laser draws ~5 W.
   Use a 5 V 4 A supply on the Jetson Nano barrel jack, not USB-C.

3. **Heat.** Sustained YOLO + cameras pushes Jetson Nano CPU+GPU > 70 °C.
   Active cooling is mandatory for >10 min runs.

4. **Encoder direction.** Quadrature decode flips sign by swapping A/B.
   If `direction: forward` produces negative pulses, change wiring or set
   `direction: reverse`.

5. **Laser baseline drifts** if the laser/camera assembly moves — re-run
   step 5 whenever you re-mount.

6. **Time sync.** Pi → Jetson timestamps will drift without NTP. Run
   `sudo apt install -y systemd-timesyncd` on each node and check
   `timedatectl status`.
