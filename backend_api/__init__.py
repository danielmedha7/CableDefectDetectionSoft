"""CableVision OS — FastAPI backend.

Bridges the inference pipeline (ZeroMQ events) to dashboard clients
(WebSocket fan-out + REST queries) and accepts raw data from the
Raspberry Pi / sensor edge nodes via HTTP ingest endpoints.
"""
