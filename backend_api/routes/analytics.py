"""Analytics — KPIs, by-class, verdicts, time-bucketed defect trend.

Returns the shape consumed by `dashboard/Analytics.jsx`:

    {
      "kpis":     { sessions, defects, pass_rate, avg_length_m },
      "by_class": [ { cls, count }, ... ],
      "verdicts": [ { verdict, count }, ... ],
      "trend":    [ { bucket, count }, ... ]
    }
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import Session as SessionORM, SurfaceDefect

router = APIRouter()


@router.get("/summary")
async def summary(
    hours: int = Query(24, ge=1, le=24 * 30),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # KPIs ─ sessions, total defects, pass rate, avg length
    n_sessions = (await db.execute(
        select(func.count(SessionORM.id)).where(SessionORM.started_at >= cutoff)
    )).scalar_one() or 0

    pass_count = (await db.execute(
        select(func.count(SessionORM.id))
        .where(SessionORM.started_at >= cutoff, SessionORM.verdict == "PASS")
    )).scalar_one() or 0

    avg_length = (await db.execute(
        select(func.avg(SessionORM.cable_length_m)).where(SessionORM.started_at >= cutoff)
    )).scalar_one() or 0.0

    n_defects = (await db.execute(
        select(func.count(SurfaceDefect.id))
        .where(SurfaceDefect.timestamp >= cutoff.timestamp())
    )).scalar_one() or 0

    pass_rate = (pass_count / n_sessions) if n_sessions else 0.0

    # by-class
    by_class_rows = (await db.execute(
        select(SurfaceDefect.cls, func.count(SurfaceDefect.id))
        .where(SurfaceDefect.timestamp >= cutoff.timestamp())
        .group_by(SurfaceDefect.cls)
        .order_by(func.count(SurfaceDefect.id).desc())
    )).all()
    by_class = [{"cls": cls, "count": int(c)} for cls, c in by_class_rows]

    # verdicts
    verdict_rows = (await db.execute(
        select(SessionORM.verdict, func.count(SessionORM.id))
        .where(SessionORM.started_at >= cutoff)
        .group_by(SessionORM.verdict)
    )).all()
    verdicts = [{"verdict": v or "UNKNOWN", "count": int(c)} for v, c in verdict_rows]

    # trend — bucket per hour
    trend = await _build_trend(db, cutoff, hours)

    return {
        "kpis": {
            "sessions": int(n_sessions),
            "defects": int(n_defects),
            "pass_rate": float(pass_rate),
            "avg_length_m": float(avg_length or 0.0),
        },
        "by_class": by_class,
        "verdicts": verdicts,
        "trend": trend,
    }


async def _build_trend(
    db: AsyncSession,
    cutoff: datetime,
    hours: int,
) -> list[dict[str, Any]]:
    """Hour-bucketed defect counts using simple Python aggregation.

    Done in-Python to stay portable across SQLite and Postgres without
    relying on database-specific date_trunc.
    """
    rows = (await db.execute(
        select(SurfaceDefect.timestamp).where(SurfaceDefect.timestamp >= cutoff.timestamp())
    )).all()

    if hours <= 24:
        bucket_seconds = 3600           # 1 h buckets for short ranges
    elif hours <= 24 * 7:
        bucket_seconds = 6 * 3600       # 6 h
    else:
        bucket_seconds = 24 * 3600      # daily

    counts: dict[int, int] = {}
    for (ts,) in rows:
        b = int(ts // bucket_seconds) * bucket_seconds
        counts[b] = counts.get(b, 0) + 1

    out: list[dict[str, Any]] = []
    for b in sorted(counts):
        out.append({
            "bucket": datetime.fromtimestamp(b, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "count": counts[b],
        })
    return out


@router.get("/by-class")
async def defects_by_class(
    hours: int = Query(24, ge=1, le=24 * 30),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
    rows = (await db.execute(
        select(SurfaceDefect.cls, func.count(SurfaceDefect.id))
        .where(SurfaceDefect.timestamp >= cutoff_ts)
        .group_by(SurfaceDefect.cls)
        .order_by(func.count(SurfaceDefect.id).desc())
    )).all()
    return [{"cls": cls, "count": int(c)} for cls, c in rows]


@router.get("/throughput")
async def throughput(
    hours: int = Query(24, ge=1, le=24 * 30),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Total cable inspected (m) and defects per metre."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    total_length = (await db.execute(
        select(func.sum(SessionORM.cable_length_m))
        .where(SessionORM.started_at >= cutoff)
    )).scalar_one() or 0.0
    n_defects = (await db.execute(
        select(func.count(SurfaceDefect.id))
        .where(SurfaceDefect.timestamp >= cutoff.timestamp())
    )).scalar_one() or 0
    return {
        "total_length_m": float(total_length),
        "defects": int(n_defects),
        "defects_per_100m": (n_defects / total_length * 100.0) if total_length else 0.0,
    }
