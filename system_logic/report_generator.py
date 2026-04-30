"""Inspection report generator.

Renders a session into JSON and a printable HTML/PDF-ready report.
PDF export is delegated to weasyprint when available; otherwise the
HTML payload is returned for client-side rendering (e.g. jsPDF).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .session_manager import Session

log = logging.getLogger(__name__)


HTML_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Inspection Report {session_id}</title>
<style>
body {{ font-family: Arial, sans-serif; color:#222; margin:32px; }}
h1   {{ color:#0a4; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
th, td {{ border: 1px solid #ccc; padding: 6px 10px; font-size: 12px; }}
th {{ background:#f3f3f3; text-align:left; }}
.verdict-pass {{ color:#0a4; font-weight:bold; }}
.verdict-fail {{ color:#c00; font-weight:bold; }}
.verdict-review {{ color:#c80; font-weight:bold; }}
.kv  {{ margin: 4px 0; }}
.kv b {{ display: inline-block; width: 160px; }}
</style></head>
<body>
<h1>CableVision OS — Inspection Report</h1>
<div class="kv"><b>Session ID:</b> {session_id}</div>
<div class="kv"><b>Cable ID:</b> {cable_id}</div>
<div class="kv"><b>Cable Spec:</b> {cable_spec}</div>
<div class="kv"><b>Operator:</b> {operator}</div>
<div class="kv"><b>Started:</b> {started_at}</div>
<div class="kv"><b>Ended:</b> {ended_at}</div>
<div class="kv"><b>Length:</b> {length_m:.2f} m</div>
<div class="kv"><b>Verdict:</b> <span class="{verdict_class}">{verdict}</span></div>
<div class="kv"><b>Reasons:</b> {reasons}</div>

<h3>Defects ({defect_count})</h3>
<table>
<thead><tr>
<th>#</th><th>Class</th><th>Severity</th><th>Pos (m)</th><th>Angle (°)</th>
<th>Depth (mm)</th><th>Area (mm²)</th><th>Conf</th><th>Cam</th>
</tr></thead>
<tbody>
{defect_rows}
</tbody>
</table>
</body></html>
"""

DEFECT_ROW = (
    "<tr><td>{i}</td><td>{cls}</td><td>{severity}</td>"
    "<td>{position_m:.2f}</td><td>{angle_deg:.1f}</td>"
    "<td>{depth_mm:.2f}</td><td>{area_mm2:.1f}</td>"
    "<td>{confidence:.2f}</td><td>{camera_id}</td></tr>"
)


class ReportGenerator:

    def __init__(self, output_dir: str | Path = "./data/reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def to_json(self, session: Session) -> dict[str, Any]:
        return session.to_dict()

    def to_html(self, session: Session) -> str:
        rows = []
        for i, d in enumerate(session.defects, start=1):
            rows.append(DEFECT_ROW.format(
                i=i,
                cls=d.get("cls", ""),
                severity=d.get("severity", ""),
                position_m=float(d.get("position_m", 0.0)),
                angle_deg=float(d.get("angle_deg", 0.0)),
                depth_mm=float(d.get("depth_mm", 0.0)),
                area_mm2=float(d.get("area_mm2", 0.0)),
                confidence=float(d.get("confidence", 0.0)),
                camera_id=d.get("camera_id", ""),
            ))

        verdict = session.verdict or "PENDING"
        return HTML_TEMPLATE.format(
            session_id=session.id,
            cable_id=session.cable_id,
            cable_spec=session.cable_spec,
            operator=session.operator,
            started_at=datetime.fromtimestamp(session.started_at).isoformat(timespec="seconds"),
            ended_at=(
                datetime.fromtimestamp(session.ended_at).isoformat(timespec="seconds")
                if session.ended_at else "-"
            ),
            length_m=session.cable_length_m,
            verdict=verdict,
            verdict_class={
                "PASS": "verdict-pass",
                "FAIL": "verdict-fail",
                "REVIEW": "verdict-review",
            }.get(verdict, ""),
            reasons=", ".join(session.verdict_reasons) or "—",
            defect_count=len(session.defects),
            defect_rows="\n".join(rows) or "<tr><td colspan='9'>No defects.</td></tr>",
        )

    def save(self, session: Session, formats: tuple[str, ...] = ("json", "html")) -> dict[str, Path]:
        out: dict[str, Path] = {}
        stem = f"{session.id}_{session.cable_id}"
        if "json" in formats:
            p = self.output_dir / f"{stem}.json"
            p.write_text(json.dumps(self.to_json(session), indent=2, default=str), encoding="utf-8")
            out["json"] = p
        if "html" in formats:
            p = self.output_dir / f"{stem}.html"
            p.write_text(self.to_html(session), encoding="utf-8")
            out["html"] = p
        if "pdf" in formats:
            try:
                from weasyprint import HTML  # optional dep
                p = self.output_dir / f"{stem}.pdf"
                HTML(string=self.to_html(session)).write_pdf(str(p))
                out["pdf"] = p
            except Exception as exc:
                log.warning("PDF export skipped (weasyprint unavailable): %s", exc)
        return out
