"""Cables + cable-spec rules.

The cable spec catalogue lives in `configs/qc_rules.yaml` and is exposed
read-only here so the dashboard can show available specs and limits.
Cable master records (per-cable metadata) live in the DB.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import Cable, CableOut, CableSpec

router = APIRouter()

QC_RULES_PATH = Path(__file__).resolve().parents[2] / "configs" / "qc_rules.yaml"


def _load_rules() -> dict[str, Any]:
    if not QC_RULES_PATH.exists():
        raise HTTPException(500, detail=f"qc_rules.yaml missing at {QC_RULES_PATH}")
    with QC_RULES_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ── spec catalogue ──
@router.get("/specs", response_model=list[CableSpec])
async def list_specs():
    rules = _load_rules()
    specs = rules.get("cable_specs", {})
    out: list[CableSpec] = []
    for name, body in specs.items():
        out.append(CableSpec(
            name=name,
            max_crack_depth_mm=float(body.get("max_crack_depth_mm", 1.5)),
            max_defects_per_100m=float(body.get("max_defects_per_100m", 3)),
            max_area_mm2=body.get("max_area_mm2"),
            max_diameter_dev_pct=body.get("max_diameter_dev_pct"),
            min_roundness=body.get("min_roundness"),
            forbidden_classes=list(body.get("forbidden_classes", [])),
        ))
    return out


@router.get("/specs/{name}", response_model=CableSpec)
async def get_spec(name: str):
    rules = _load_rules()
    spec = rules.get("cable_specs", {}).get(name)
    if spec is None:
        raise HTTPException(404, detail=f"cable spec '{name}' not found")
    return CableSpec(
        name=name,
        max_crack_depth_mm=float(spec.get("max_crack_depth_mm", 1.5)),
        max_defects_per_100m=float(spec.get("max_defects_per_100m", 3)),
        max_area_mm2=spec.get("max_area_mm2"),
        max_diameter_dev_pct=spec.get("max_diameter_dev_pct"),
        min_roundness=spec.get("min_roundness"),
        forbidden_classes=list(spec.get("forbidden_classes", [])),
    )


# ── cable master records ──
@router.get("", response_model=list[CableOut])
async def list_cables(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Cable).order_by(Cable.created_at.desc()))).scalars().all()
    return [CableOut.model_validate(r) for r in rows]


@router.post("", response_model=CableOut, status_code=201)
async def upsert_cable(cable: CableOut, db: AsyncSession = Depends(get_session)):
    existing = await db.get(Cable, cable.id)
    if existing is None:
        row = Cable(id=cable.id, spec=cable.spec, notes=cable.notes or "")
        db.add(row)
    else:
        existing.spec = cable.spec
        existing.notes = cable.notes or ""
        row = existing
    await db.flush()
    await db.refresh(row)
    return CableOut.model_validate(row)


@router.get("/{cable_id}", response_model=CableOut)
async def get_cable(cable_id: str, db: AsyncSession = Depends(get_session)):
    row = await db.get(Cable, cable_id)
    if row is None:
        raise HTTPException(404, detail=f"cable {cable_id} not found")
    return CableOut.model_validate(row)


@router.delete("/{cable_id}", status_code=204)
async def delete_cable(cable_id: str, db: AsyncSession = Depends(get_session)):
    row = await db.get(Cable, cable_id)
    if row is None:
        raise HTTPException(404, detail=f"cable {cable_id} not found")
    await db.delete(row)
    return None
