"""Sessions — list, get, create, end, delete."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_session
from ..models import (
    Session as SessionORM,
    SessionCreate,
    SessionDetail,
    SessionEnd,
    SessionSummary,
    SurfaceDefect,
    InternalDefect as InternalDefectORM,
)

router = APIRouter()


@router.get("", response_model=list[SessionSummary])
async def list_sessions(
    limit: int = Query(50, ge=1, le=500),
    cable_id: str | None = None,
    verdict: str | None = None,
    db: AsyncSession = Depends(get_session),
):
    stmt = select(SessionORM).order_by(SessionORM.started_at.desc()).limit(limit)
    if cable_id:
        stmt = stmt.where(SessionORM.cable_id == cable_id)
    if verdict:
        stmt = stmt.where(SessionORM.verdict == verdict)
    rows = (await db.execute(stmt)).scalars().all()
    return [SessionSummary.model_validate(r) for r in rows]


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session_detail(session_id: str, db: AsyncSession = Depends(get_session)):
    stmt = (
        select(SessionORM)
        .options(
            selectinload(SessionORM.surface_defects),
            selectinload(SessionORM.internal_defects),
        )
        .where(SessionORM.id == session_id)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail=f"session {session_id} not found")

    return SessionDetail(
        id=row.id,
        cable_id=row.cable_id,
        cable_spec=row.cable_spec,
        operator=row.operator,
        started_at=row.started_at,
        ended_at=row.ended_at,
        cable_length_m=row.cable_length_m,
        verdict=row.verdict,
        verdict_reasons=row.verdict_reasons or [],
        notes=row.notes or "",
        defects=[_surface_to_pyd(d) for d in row.surface_defects],
        internal_defects=[_internal_to_pyd(d) for d in row.internal_defects],
    )


@router.post("", response_model=SessionSummary, status_code=201)
async def create_session(payload: SessionCreate, db: AsyncSession = Depends(get_session)):
    session_id = payload.id or uuid.uuid4().hex[:12]
    sess = SessionORM(
        id=session_id,
        cable_id=payload.cable_id,
        cable_spec=payload.cable_spec,
        operator=payload.operator,
        notes=payload.notes,
        started_at=datetime.now(timezone.utc),
        verdict_reasons=[],
    )
    db.add(sess)
    await db.flush()
    await db.refresh(sess)
    return SessionSummary.model_validate(sess)


@router.post("/{session_id}/end", response_model=SessionSummary)
async def end_session(session_id: str, payload: SessionEnd, db: AsyncSession = Depends(get_session)):
    sess = await db.get(SessionORM, session_id)
    if sess is None:
        raise HTTPException(404, detail=f"session {session_id} not found")
    sess.verdict = payload.verdict
    sess.verdict_reasons = payload.verdict_reasons
    sess.cable_length_m = payload.cable_length_m
    sess.ended_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(sess)
    return SessionSummary.model_validate(sess)


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: str, db: AsyncSession = Depends(get_session)):
    sess = await db.get(SessionORM, session_id)
    if sess is None:
        raise HTTPException(404, detail=f"session {session_id} not found")
    await db.delete(sess)
    return None


# ── helpers ──
def _surface_to_pyd(d: SurfaceDefect):
    from ..models import SurfaceDefectOut
    return SurfaceDefectOut.model_validate(d)


def _internal_to_pyd(d: InternalDefectORM):
    from ..models import InternalDefectOut
    return InternalDefectOut.model_validate(d)


# unused but explicit — guarantees the timestamp helper is importable
_now = time.time
