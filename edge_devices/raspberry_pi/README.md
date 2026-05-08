# Raspberry Pi Camera Streamer

Runs on each Raspberry Pi and streams two real camera feeds to the Jetson receiver.

## Pi Setup

Install the runtime dependencies on Raspberry Pi OS:

```bash
sudo apt update
sudo apt install -y python3-opencv python3-picamera2 python3-yaml python3-requests
```

## Run

Edit `camera_config.yaml` first:

```yaml
device:
  node_id: pi_a

streaming:
  jetson_frame_endpoint: "http://JETSON_IP_ADDRESS:8000/ingest/frame"
  send_enabled: true
```

Then run:

```bash
python3 camera_streamer.py
```

Pi A should own `CAM-1` and `CAM-2`. Pi B should use a copied config with `node_id: pi_b` and camera IDs `CAM-3` and `CAM-4`.

## Camera Sources

For Raspberry Pi Camera Modules:

```yaml
source_type: "picamera2"
source: 0
```

For USB cameras:

```yaml
source_type: "opencv"
source: 0
```

The streamer sends each frame as `multipart/form-data` to:

```text
POST /ingest/frame
```

with metadata fields: `node_id`, `camera_id`, `frame_id`, `timestamp_ns`, `width`, `height`, and `format`.
