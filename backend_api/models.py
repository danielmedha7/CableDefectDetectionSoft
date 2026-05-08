"""Database (ORM) + API (Pydantic) models.

Two halves of one file:
  • ORM classes — persisted via SQLAlchemy 2.0
  • Schema classes — request/response payloads via Pydantic v2

Tables:
  cables             — cable spec / SKU master
  sessions           — one inspection run
  surface_defects    — YOLO + laser fused detections
  internal_defects   — XGBoost ultrasonic classifications
  encoder_samples    — raw encoder snapshots (optional)
  ultrasonic_windows — raw A-scan windows (optional, can grow large)
"""
"""Note: do NOT add `from __future__ import annotations` here.

SQLAlchemy 2.0 introspects `Mapped[...]` types at registration via
`get_type_hints()`. With deferred evaluation, that resolves the string
"str | None" — which fails to evaluate on Python 3.8/3.9 (PEP 604 syntax
landed in 3.10). Using `Optional[str]` keeps the file portable from
Python 3.8 (Jetpack 5 default) all the way up.
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


# ───────────────────────── ORM ─────────────────────────

class Cable(Base):
    __tablename__ = "cables"

    id:        Mapped[str]  = mapped_column(String(64), primary_key=True)
    spec:      Mapped[str]  = mapped_column(String(32), default="default")
    notes:     Mapped[str]  = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sessions: Mapped[list["Session"]] = relationship(back_populates="cable")


class Session(Base):
    __tablename__ = "sessions"

    id:           Mapped[str]   = mapped_column(String(32), primary_key=True)
    cable_id:     Mapped[str]   = mapped_column(String(64), ForeignKey("cables.id"), index=True)
    cable_spec:   Mapped[str]   = mapped_column(String(32), default="default")
    operator:     Mapped[str]   = mapped_column(String(64), default="system")
    started_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at:     Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cable_length_m: Mapped[float] = mapped_column(Float, default=0.0)
    verdict:      Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    verdict_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes:        Mapped[str]   = mapped_column(String(512), default="")

    cable:  Mapped[Cable] = relationship(back_populates="sessions")
    surface_defects:  Mapped[list["SurfaceDefect"]]  = relationship(back_populates="session", cascade="all, delete-orphan")
    internal_defects: Mapped[list["InternalDefect"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class SurfaceDefect(Base):
    __tablename__ = "surface_defects"

    id:         Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str]   = mapped_column(String(32), ForeignKey("sessions.id"), index=True)
    timestamp:  Mapped[float] = mapped_column(Float)
    cls:        Mapped[str]   = mapped_column(String(32), index=True)
    severity:   Mapped[str]   = mapped_column(String(16), default="low", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    angle_deg:  Mapped[float] = mapped_column(Float, default=0.0)
    position_m: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    depth_mm:   Mapped[float] = mapped_column(Float, default=0.0)
    area_mm2:   Mapped[float] = mapped_column(Float, default=0.0)
    camera_id:  Mapped[int]   = mapped_column(Integer, default=0)
    bbox:       Mapped[list[int]] = mapped_column(JSON, default=list)
    verdict:    Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    extra:      Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    session: Mapped[Session] = relationship(back_populates="surface_defects")


class InternalDefect(Base):
    __tablename__ = "internal_defects"

    id:         Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("sessions.id"), index=True, nullable=True)
    timestamp:  Mapped[float] = mapped_column(Float)
    cls:        Mapped[str]   = mapped_column(String(32), index=True)
    severity:   Mapped[str]   = mapped_column(String(16), default="low")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    depth_mm:   Mapped[float] = mapped_column(Float, default=0.0)
    size_mm:    Mapped[float] = mapped_column(Float, default=0.0)
    position_m: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    channel:    Mapped[int]   = mapped_column(Integer, default=0)
    extra:      Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    session: Mapped[Optional[Session]] = relationship(back_populates="internal_defects")


class EncoderSample(Base):
    __tablename__ = "encoder_samples"

    id:           Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp:    Mapped[float] = mapped_column(Float, index=True)
    pulses:       Mapped[int]   = mapped_column(Integer)
    delta_pulses: Mapped[int]   = mapped_column(Integer, default=0)
    position_m:   Mapped[float] = mapped_column(Float, default=0.0)
    speed_mps:    Mapped[float] = mapped_column(Float, default=0.0)


class UltrasonicWindow(Base):
    __tablename__ = "ultrasonic_windows"
    __table_args__ = (Index("ix_us_ts_ch", "timestamp", "channel"),)

    id:         Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp:  Mapped[float] = mapped_column(Float)
    channel:    Mapped[int]   = mapped_column(Integer)
    sample_rate_hz: Mapped[int] = mapped_column(Integer, default=1_000_000)
    n_samples:  Mapped[int]   = mapped_column(Integer, default=0)
    samples:    Mapped[list[float]] = mapped_column(JSON, default=list)


# ───────────────────────── Pydantic schemas ─────────────────────────

class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CableOut(_Base):
    id: str
    spec: str
    notes: str = ""
    created_at: Optional[datetime] = None


class CableSpec(_Base):
    name: str
    max_crack_depth_mm: float
    max_defects_per_100m: float
    max_area_mm2: Optional[float] = None
    max_diameter_dev_pct: Optional[float] = None
    min_roundness: Optional[float] = None
    forbidden_classes: list[str] = []


class SurfaceDefectOut(_Base):
    id: int
    session_id: str
    timestamp: float
    cls: str
    severity: str
    confidence: float
    angle_deg: float
    position_m: float
    depth_mm: float
    area_mm2: float
    camera_id: int
    bbox: list[int] = []
    verdict: Optional[str] = None


class InternalDefectIn(_Base):
    timestamp: Optional[float] = None
    cls: str
    severity: str = "low"
    confidence: float = 0.0
    depth_mm: float = 0.0
    size_mm: float = 0.0
    position_m: float = 0.0
    channel: int = 0
    session_id: Optional[str] = None
    extra: dict[str, Any] = {}


class InternalDefectOut(_Base):
    id: int
    session_id: Optional[str] = None
    timestamp: float
    cls: str
    severity: str
    confidence: float
    depth_mm: float
    size_mm: float
    position_m: float
    channel: int


class SessionSummary(_Base):
    id: str
    cable_id: str
    cable_spec: str
    operator: str
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    cable_length_m: float
    verdict: Optional[str] = None


class SessionDetail(SessionSummary):
    verdict_reasons: list[str] = []
    notes: str = ""
    defects: list[SurfaceDefectOut] = []
    internal_defects: list[InternalDefectOut] = []


class SessionCreate(_Base):
    id: Optional[str] = None
    cable_id: str
    cable_spec: str = "default"
    operator: str = "system"
    notes: str = ""


class SessionEnd(_Base):
    verdict: str
    verdict_reasons: list[str] = []
    cable_length_m: float = 0.0


class EncoderIn(_Base):
    timestamp: float
    pulses: int
    delta_pulses: int = 0
    position_m: float = 0.0
    speed_mps: float = 0.0


class UltrasonicIn(_Base):
    timestamp: float
    sample_rate_hz: int = 1_000_000
    gate_start_us: float = 10.0
    gate_end_us:   float = 120.0
    channels: dict[str, list[float]]
