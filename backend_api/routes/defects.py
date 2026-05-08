"""Defect listing — surface (YOLO) and internal (XGBoost) records."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import (
    InternalDefect as InternalDefectORM,
    InternalDefectIn,
    InternalDefectOut,
    SurfaceDefect,
    SurfaceDefectOut,
)

router = APIRouter()


# ── surface ──
@router.get("/surface", response_model=list[SurfaceDefectOut])
async def list_surface(
    limit: int = Query(200, ge=1, le=2000),
    session_id: str | None = None,
    cls: str | None = None,
    severity: str | None = None,
    db: AsyncSession = Depends(get_session),
):
    stmt = select(SurfaceDefect).order_by(SurfaceDefect.timestamp.desc()).limit(limit)
    if session_id:
        stmt = stmt.where(SurfaceDefect.session_id == session_id)
    if cls:
        stmt = stmt.where(SurfaceDefect.cls == cls)
    if severity:
        stmt = stmt.where(SurfaceDefect.severity == severity)
    rows = (await db.execute(stmt)).scalars().all()
    return [SurfaceDefectOut.model_validate(r) for r in rows]


@router.get("/surface/{defect_id}", response_model=SurfaceDefectOut)
async def get_surface(defect_id: int, db: AsyncSession = Depends(get_session)):
    row = await db.get(SurfaceDefect, defect_id)
    if row is None:
        raise HTTPException(404, detail=f"surface defect {defect_id} not found")
    return SurfaceDefectOut.model_validate(row)


# ── internal ──
@router.get("/internal", response_model=list[InternalDefectOut])
async def list_internal(
    limit: int = Query(200, ge=1, le=2000),
    session_id: str | None = None,
    cls: str | None = None,
    db: AsyncSession = Depends(get_session),
):
    stmt = select(InternalDefectORM).order_by(InternalDefectORM.timestamp.desc()).limit(limit)
    if session_id:
        stmt = stmt.where(InternalDefectORM.session_id == session_id)
    if cls:
        stmt = stmt.where(InternalDefectORM.cls == cls)
    rows = (await db.execute(stmt)).scalars().all()
    return [InternalDefectOut.model_validate(r) for r in rows]


@router.get("/internal/{defect_id}", response_model=InternalDefectOut)
async def get_internal(defect_id: int, db: AsyncSession = Depends(get_session)):
    row = await db.get(InternalDefectORM, defect_id)
    if row is None:
        raise HTTPException(404, detail=f"internal defect {defect_id} not found")
    return InternalDefectOut.model_validate(row)


@router.post("/internal", response_model=InternalDefectOut, status_code=201)
async def create_internal(payload: InternalDefectIn, db: AsyncSession = Depends(get_session)):
    row = InternalDefectORM(
        session_id=payload.session_id,
        timestamp=payload.timestamp if payload.timestamp is not None else time.time(),
        cls=payload.cls,
        severity=payload.severity,
        confidence=payload.confidence,
        depth_mm=payload.depth_mm,
        size_mm=payload.size_mm,
        position_m=payload.position_m,
        channel=payload.channel,
        extra=payload.extra,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return InternalDefectOut.model_validate(row)
