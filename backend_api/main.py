"""FastAPI entry-point.

Responsibilities
----------------
1. Mount REST routes (sessions, cables, defects, analytics).
2. Run a WebSocket fan-out at `/ws` for the dashboard.
3. Subscribe to the inference pipeline's ZeroMQ PUB socket and forward
   each event to all connected WebSocket clients.
4. Accept raw data ingest from the edge devices:
       POST /ingest/frame       (multipart, JPEG)
       POST /ingest/encoder     (JSON, EncoderIn)
       POST /ingest/ultrasonic  (JSON, UltrasonicIn)
5. Serve a basic MJPEG passthrough at `/api/cameras/{cam_id}/stream`
   if a frame buffer is populated by ingest.

Run:
    uvicorn backend_api.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .database import init_db, shutdown_db, AsyncSessionLocal
from .models import EncoderIn, EncoderSample, UltrasonicIn, UltrasonicWindow
from .routes import analytics, cables, defects, sessions

log = logging.getLogger("backend_api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s :: %(message)s")


# ──────────────── config ────────────────
def _load_config() -> dict[str, Any]:
    p = Path(__file__).resolve().parents[1] / "configs" / "config.yaml"
    if not p.exists():
        log.warning("configs/config.yaml not found — using defaults")
        return {}
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


CONFIG = _load_config()
API_CFG = CONFIG.get("api", {})
IPC_CFG = CONFIG.get("ipc", {})


# ──────────────── WebSocket fan-out ────────────────
class WSManager:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
        log.info("WS client +1 (total=%d)", len(self._clients))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
        log.info("WS client -1 (total=%d)", len(self._clients))

    async def broadcast(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, default=str)
        async with self._lock:
            clients = list(self._clients)
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


ws_manager = WSManager()


# ──────────────── ZMQ → WebSocket bridge ────────────────
async def _zmq_bridge(stop: asyncio.Event) -> None:
    """Subscribe to the pipeline's PUB socket and forward messages to WS."""
    addr = IPC_CFG.get("zmq_sub_addr", "tcp://127.0.0.1:5556")
    try:
        import zmq
        import zmq.asyncio
    except ImportError:
        log.warning("pyzmq not installed — ZMQ bridge disabled")
        return

    ctx = zmq.asyncio.Context.instance()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt_string(zmq.SUBSCRIBE, "")
    try:
        sock.connect(addr)
    except Exception as exc:
        log.warning("ZMQ connect failed (%s) — bridge offline", exc)
        return

    log.info("ZMQ bridge subscribed @ %s", addr)
    try:
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(sock.recv_string(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                log.warning("ZMQ recv failed: %s", exc)
                await asyncio.sleep(0.5)
                continue

            # frame format: "<topic> <json>"
            _, _, payload = raw.partition(" ")
            try:
                envelope = json.loads(payload)
            except json.JSONDecodeError:
                continue

            await ws_manager.broadcast(envelope)
            # fire-and-forget: a slow DB write must not stall the WS bridge
            asyncio.create_task(_persist_event(envelope))
    finally:
        try:
            sock.close(linger=0)
        except Exception:
            pass
        log.info("ZMQ bridge stopped")


async def _persist_event(envelope: dict[str, Any]) -> None:
    """Mirror live events into the database so REST queries see them."""
    etype = envelope.get("type")
    payload = envelope.get("payload") or {}

    try:
        async with AsyncSessionLocal() as db:
            if etype == "defect_detected":
                from .models import SurfaceDefect
                d = payload.get("defect") or {}
                if not d.get("session_id"):
                    return
                row = SurfaceDefect(
                    session_id=d["session_id"],
                    timestamp=float(d.get("timestamp", time.time())),
                    cls=str(d.get("cls", "unknown")),
                    severity=str(d.get("severity", "low")),
                    confidence=float(d.get("confidence", 0.0)),
                    angle_deg=float(d.get("angle_deg", 0.0)),
                    position_m=float(d.get("position_m", 0.0)),
                    depth_mm=float(d.get("depth_mm", 0.0)),
                    area_mm2=float(d.get("area_mm2", 0.0)),
                    camera_id=int(d.get("camera_id", 0)),
                    bbox=list(d.get("bbox", []) or []),
                    verdict=payload.get("verdict"),
                    extra=d.get("extra") or {},
                )
                db.add(row)
                await db.commit()
            elif etype == "internal_defect":
                from .models import InternalDefect as InternalDefectORM
                row = InternalDefectORM(
                    session_id=payload.get("session_id"),
                    timestamp=float(payload.get("timestamp", time.time())),
                    cls=str(payload.get("cls", "unknown")),
                    severity=str(payload.get("severity", "low")),
                    confidence=float(payload.get("confidence", 0.0)),
                    depth_mm=float(payload.get("depth_mm", 0.0)),
                    size_mm=float(payload.get("size_mm", 0.0)),
                    position_m=float(payload.get("position_m", 0.0)),
                    channel=int(payload.get("channel", 0)),
                    extra=payload.get("extra") or {},
                )
                db.add(row)
                await db.commit()
    except Exception:
        log.exception("event persist failed (type=%s)", etype)


# ──────────────── frame buffer for camera passthrough ────────────────
_LATEST_FRAME: dict[int, bytes] = {}
_LATEST_FRAME_LOCK = asyncio.Lock()


# ──────────────── lifespan ────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    stop = asyncio.Event()
    bridge_task = asyncio.create_task(_zmq_bridge(stop))
    log.info("Backend started — REST + WS @ %s:%s",
             API_CFG.get("host", "0.0.0.0"), API_CFG.get("port", 8000))
    try:
        yield
    finally:
        stop.set()
        await asyncio.wait_for(bridge_task, timeout=2.0)
        await shutdown_db()
        log.info("Backend stopped")


# ──────────────── app ────────────────
app = FastAPI(
    title="CableVision OS API",
    version="3.0",
    description="REST + WebSocket gateway for the CableVision inspection pipeline.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=API_CFG.get("cors_origins", ["*"]) or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
app.include_router(cables.router,   prefix="/api/cables",   tags=["cables"])
app.include_router(defects.router,  prefix="/api/defects",  tags=["defects"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])


# ──────────────── basic ────────────────
@app.get("/", tags=["meta"])
async def root() -> dict[str, Any]:
    return {
        "service": "cablevision-os-api",
        "version": app.version,
        "status": "ok",
        "ws_clients": len(ws_manager._clients),
    }


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "healthy"}


# ──────────────── WebSocket ────────────────
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await ws_manager.connect(websocket)
    try:
        # Heartbeat / drain incoming messages so the socket stays alive
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                if msg == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "heartbeat", "ts": time.time()}))
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("WS handler error")
    finally:
        await ws_manager.disconnect(websocket)


# ──────────────── ingest from edge devices ────────────────
@app.post("/ingest/frame", tags=["ingest"])
async def ingest_frame(
    node_id: str = Form(...),
    camera_id: str = Form(...),
    frame_id: int = Form(0),
    timestamp_ns: int = Form(0),
    width: int = Form(0),
    height: int = Form(0),
    frame: UploadFile = File(...),
) -> dict[str, Any]:
    data = await frame.read()
    try:
        cam_int = int(camera_id) if camera_id.isdigit() else hash(camera_id) % 4
    except Exception:
        cam_int = 0
    async with _LATEST_FRAME_LOCK:
        _LATEST_FRAME[cam_int] = data

    await ws_manager.broadcast({
        "type": "frame_meta",
        "ts": time.time(),
        "payload": {
            "node_id": node_id,
            "camera_id": camera_id,
            "frame_id": frame_id,
            "timestamp_ns": timestamp_ns,
            "width": width,
            "height": height,
            "bytes": len(data),
        },
    })
    return {"ok": True, "size": len(data), "camera_id": camera_id}


@app.post("/ingest/encoder", tags=["ingest"])
async def ingest_encoder(payload: EncoderIn) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        row = EncoderSample(
            timestamp=payload.timestamp,
            pulses=payload.pulses,
            delta_pulses=payload.delta_pulses,
            position_m=payload.position_m,
            speed_mps=payload.speed_mps,
        )
        db.add(row)
        await db.commit()

    await ws_manager.broadcast({
        "type": "encoder",
        "ts": payload.timestamp,
        "payload": payload.model_dump(),
    })
    return {"ok": True}


@app.post("/ingest/ultrasonic", tags=["ingest"])
async def ingest_ultrasonic(payload: UltrasonicIn) -> dict[str, Any]:
    """Persist one acquisition cycle (one row per channel)."""
    persisted = 0
    async with AsyncSessionLocal() as db:
        for ch_str, samples in payload.channels.items():
            try:
                ch = int(ch_str)
            except ValueError:
                continue
            row = UltrasonicWindow(
                timestamp=payload.timestamp,
                channel=ch,
                sample_rate_hz=payload.sample_rate_hz,
                n_samples=len(samples),
                samples=list(samples),
            )
            db.add(row)
            persisted += 1
        await db.commit()
    return {"ok": True, "channels_persisted": persisted}


# ──────────────── camera passthrough ────────────────
@app.get("/api/cameras/{cam_id}/snapshot", tags=["cameras"])
async def camera_snapshot(cam_id: int):
    async with _LATEST_FRAME_LOCK:
        data = _LATEST_FRAME.get(cam_id)
    if not data:
        raise HTTPException(404, detail=f"no frame for cam_id={cam_id}")
    return StreamingResponse(_iter_bytes(data), media_type="image/jpeg")


def _iter_bytes(data: bytes):
    yield data


@app.get("/api/cameras/{cam_id}/stream", tags=["cameras"])
async def camera_stream(cam_id: int):
    async def gen():
        boundary = b"--cv_boundary"
        while True:
            async with _LATEST_FRAME_LOCK:
                data = _LATEST_FRAME.get(cam_id)
            if data:
                yield (
                    boundary + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n"
                    + data + b"\r\n"
                )
            await asyncio.sleep(0.05)
    return StreamingResponse(
        gen(),
        media_type="multipart/x-mixed-replace; boundary=cv_boundary",
    )


# ──────────────── dashboard_app snapshot ────────────────
@app.get("/dashboard/snapshot", tags=["dashboard"])
async def dashboard_snapshot() -> dict[str, Any]:
    """Single-shot telemetry blob consumed by `dashboard_app/src/services/telemetry.js`.

    Combines the latest session, position, measurements, recent defects, and
    the last frame metadata for each camera. Polled at ~1 Hz.
    """
    from sqlalchemy import select
    from .models import (
        Session as SessionORM, SurfaceDefect, EncoderSample,
    )

    snapshot: dict[str, Any] = {
        "updatedAt": time.strftime("%H:%M:%S"),
        "session": {
            "id": "—",
            "status": "IDLE",
            "cableType": "—",
            "operator": "—",
            "startedAt": "—",
            "inspectedLengthM": 0.0,
            "lineSpeedMMin": 0.0,
        },
        "measurements": {
            "diameterMm": 0.0,
            "targetDiameterMm": 35.0,
            "toleranceMm": 0.5,
            "roundnessMm": 0.0,
            "surfaceScore": 0,
            "internalRisk": 0,
        },
        "cameras": [
            {
                "id": f"cam-{i}",
                "name": f"Camera {i}",
                "fps": 30,
                "exposureMs": 4,
                "signal": 100 if i in _LATEST_FRAME else 0,
                "frameAgeMs": 0,
                "laserLocked": True,
                "previewUrl": f"/api/cameras/{i}/snapshot",
            }
            for i in range(4)
        ],
        "devices": {
            "jetson": {"status": "Online", "load": 25},
            "piA":    {"status": "Online", "latencyMs": 4},
            "piB":    {"status": "Online", "latencyMs": 5},
        },
        "profile": [],
        "defects": [],
    }

    async with AsyncSessionLocal() as db:
        # latest session
        last = (await db.execute(
            select(SessionORM).order_by(SessionORM.started_at.desc()).limit(1)
        )).scalar_one_or_none()
        if last is not None:
            snapshot["session"] = {
                "id": last.id,
                "status": last.verdict or "RUNNING",
                "cableType": last.cable_spec,
                "operator": last.operator,
                "startedAt": last.started_at.isoformat() if last.started_at else "—",
                "inspectedLengthM": float(last.cable_length_m or 0.0),
                "lineSpeedMMin": 0.0,
            }

        # recent encoder snapshot for line speed
        enc = (await db.execute(
            select(EncoderSample).order_by(EncoderSample.timestamp.desc()).limit(1)
        )).scalar_one_or_none()
        if enc is not None:
            snapshot["session"]["lineSpeedMMin"] = round(enc.speed_mps * 60.0, 2)

        # recent defects (last 30)
        defects = (await db.execute(
            select(SurfaceDefect).order_by(SurfaceDefect.timestamp.desc()).limit(30)
        )).scalars().all()
        snapshot["defects"] = [
            {
                "id": d.id,
                "type": d.cls,
                "severity": d.severity,
                "positionMm": int(d.position_m * 1000),
                "camera": f"cam-{d.camera_id}",
                "confidence": int(d.confidence * 100),
                "status": d.verdict or "REVIEW",
            }
            for d in defects
        ]
    return snapshot


# ──────────────── dev convenience ────────────────
@app.post("/dev/broadcast", tags=["dev"])
async def dev_broadcast(payload: dict[str, Any]) -> dict[str, Any]:
    """Inject an event onto the WS bus (for testing without the pipeline)."""
    if os.environ.get("CABLEVISION_DISABLE_DEV_ROUTES") == "1":
        raise HTTPException(403, detail="dev routes disabled")
    await ws_manager.broadcast(payload)
    return {"ok": True}
