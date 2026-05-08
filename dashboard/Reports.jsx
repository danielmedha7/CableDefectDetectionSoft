// ─────────────────────────────────────────────
// Reports.jsx — list past sessions, view + export PDF
// ─────────────────────────────────────────────
import React, { useEffect, useState } from "react";
import jsPDF from "jspdf";
import { api, verdictColor } from "./hooks";

export default function Reports() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    let alive = true;
    api.get("/api/sessions?limit=100")
      .then(r => { if (alive) setSessions(r.data ?? []); })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  async function open(id) {
    const r = await api.get(`/api/sessions/${id}`);
    setSelected(r.data);
  }

  function exportPdf() {
    if (!selected) return;
    const doc = new jsPDF();
    let y = 16;
    doc.setFontSize(18);
    doc.text("CableVision OS — Inspection Report", 14, y); y += 10;
    doc.setFontSize(11);
    [
      ["Session", selected.id],
      ["Cable",   selected.cable_id],
      ["Spec",    selected.cable_spec],
      ["Operator",selected.operator],
      ["Length",  `${selected.cable_length_m?.toFixed?.(2) ?? "—"} m`],
      ["Verdict", selected.verdict ?? "PENDING"],
      ["Reasons", (selected.verdict_reasons ?? []).join(", ") || "—"],
    ].forEach(([k, v]) => { doc.text(`${k}: ${v}`, 14, y); y += 6; });

    y += 4;
    doc.setFontSize(13); doc.text(`Defects (${selected.defects?.length ?? 0})`, 14, y); y += 6;
    doc.setFontSize(9);
    (selected.defects ?? []).forEach((d, i) => {
      if (y > 280) { doc.addPage(); y = 16; }
      doc.text(
        `${i + 1}. ${d.cls} · ${d.severity} · ${d.position_m?.toFixed?.(2)}m · `
        + `${d.angle_deg?.toFixed?.(1)}° · depth=${d.depth_mm?.toFixed?.(2)}mm · conf=${d.confidence?.toFixed?.(2)}`,
        14, y,
      );
      y += 5;
    });
    doc.save(`session_${selected.id}.pdf`);
  }

  return (
    <div className="p-6 text-slate-200 bg-slate-950 min-h-screen grid grid-cols-1 md:grid-cols-3 gap-4">
      <aside className="md:col-span-1">
        <h1 className="text-2xl font-bold mb-4">Reports</h1>
        {loading && <p className="text-slate-400 text-sm">Loading…</p>}
        <ul className="space-y-1 max-h-[80vh] overflow-y-auto">
          {sessions.map(s => (
            <li key={s.id}>
              <button
                onClick={() => open(s.id)}
                className={`w-full text-left px-3 py-2 rounded text-xs font-mono border ${
                  selected?.id === s.id
                    ? "bg-emerald-500/10 border-emerald-500/40"
                    : "bg-slate-900 border-slate-800 hover:border-slate-600"
                }`}
              >
                <div className="flex justify-between">
                  <span className="truncate">{s.cable_id}</span>
                  <span className={`px-1.5 rounded border ${verdictColor(s.verdict)}`}>
                    {s.verdict ?? "—"}
                  </span>
                </div>
                <div className="text-[10px] text-slate-500">{s.id}</div>
              </button>
            </li>
          ))}
          {sessions.length === 0 && !loading && (
            <li className="text-slate-500 text-sm">No sessions yet.</li>
          )}
        </ul>
      </aside>

      <main className="md:col-span-2 bg-slate-900 border border-slate-800 rounded-md p-4 min-h-[60vh]">
        {!selected && <p className="text-slate-500 text-sm">Pick a session on the left.</p>}
        {selected && (
          <>
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-lg font-bold">{selected.cable_id}</h2>
                <p className="text-slate-500 text-xs font-mono">{selected.id}</p>
              </div>
              <button
                onClick={exportPdf}
                className="bg-emerald-500/20 border border-emerald-500/40 text-emerald-300 px-3 py-1.5 rounded text-sm hover:bg-emerald-500/30"
              >
                Export PDF
              </button>
            </div>

            <div className="grid grid-cols-2 gap-3 mb-4 text-sm">
              <Field label="Spec"     value={selected.cable_spec} />
              <Field label="Operator" value={selected.operator} />
              <Field label="Length"   value={`${selected.cable_length_m?.toFixed?.(2) ?? "—"} m`} />
              <Field
                label="Verdict"
                value={
                  <span className={`px-2 py-0.5 rounded border ${verdictColor(selected.verdict)}`}>
                    {selected.verdict ?? "PENDING"}
                  </span>
                }
              />
            </div>

            <h3 className="text-xs uppercase tracking-widest text-slate-400 mb-2">
              Defects ({selected.defects?.length ?? 0})
            </h3>
            <div className="rounded-md border border-slate-800 overflow-hidden">
              <table className="w-full text-xs font-mono">
                <thead className="bg-slate-950 text-slate-400">
                  <tr>
                    <th className="text-left px-3 py-2">#</th>
                    <th className="text-left px-3 py-2">Class</th>
                    <th className="text-right px-3 py-2">Pos (m)</th>
                    <th className="text-right px-3 py-2">Depth</th>
                    <th className="text-right px-3 py-2">Conf</th>
                  </tr>
                </thead>
                <tbody>
                  {(selected.defects ?? []).map((d, i) => (
                    <tr key={i} className="border-t border-slate-800">
                      <td className="px-3 py-1.5">{i + 1}</td>
                      <td className="px-3 py-1.5">{d.cls}</td>
                      <td className="px-3 py-1.5 text-right">{d.position_m?.toFixed?.(2)}</td>
                      <td className="px-3 py-1.5 text-right">{d.depth_mm?.toFixed?.(2)}</td>
                      <td className="px-3 py-1.5 text-right">{d.confidence?.toFixed?.(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

function Field({ label, value }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-widest text-slate-500">{label}</div>
      <div>{value}</div>
    </div>
  );
}
