// ─────────────────────────────────────────────
// LiveMonitor.jsx — 4 camera feeds + real-time defect overlay
// ─────────────────────────────────────────────
import React, { useMemo } from "react";
import { useDetections, severityColor } from "./hooks";

const API_BASE = import.meta.env?.VITE_API_BASE ?? "http://localhost:8000";

function CameraTile({ camId, detections }) {
  const camDets = useMemo(
    () => detections.filter(d => d.defect?.camera_id === camId).slice(-3),
    [detections, camId],
  );
  return (
    <div className="relative aspect-video rounded-md overflow-hidden border border-slate-700 bg-slate-900">
      <img
        src={`${API_BASE}/api/cameras/${camId}/stream`}
        alt={`cam ${camId}`}
        className="w-full h-full object-cover"
        onError={(e) => { e.currentTarget.style.opacity = 0.2; }}
      />
      <div className="absolute top-2 left-2 px-2 py-0.5 rounded bg-black/60 text-emerald-300 text-xs font-mono">
        CAM {camId}
      </div>
      <div className="absolute bottom-2 left-2 right-2 flex gap-1 flex-wrap">
        {camDets.map((d, i) => (
          <span
            key={i}
            className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${severityColor(d.defect.severity)}`}
            title={`${d.defect.cls} @ ${d.defect.position_m?.toFixed(2)}m`}
          >
            {d.defect.cls}
          </span>
        ))}
      </div>
    </div>
  );
}

export default function LiveMonitor() {
  const { connected, detections } = useDetections(80);
  const recent = detections.slice(-12).reverse();

  return (
    <div className="p-6 text-slate-200 bg-slate-950 min-h-screen">
      <header className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-wide">Live Monitor</h1>
          <p className="text-slate-400 text-xs">Real-time inspection · 4× IMX219</p>
        </div>
        <span className={`px-3 py-1 rounded text-xs font-mono border ${
          connected
            ? "text-emerald-300 border-emerald-500/40 bg-emerald-500/10"
            : "text-rose-300 border-rose-500/40 bg-rose-500/10"
        }`}>
          {connected ? "● LIVE" : "○ OFFLINE"}
        </span>
      </header>

      <section className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-6">
        {[0, 1, 2, 3].map(id => (
          <CameraTile key={id} camId={id} detections={detections} />
        ))}
      </section>

      <section>
        <h2 className="text-sm font-bold text-slate-300 uppercase tracking-widest mb-3">
          Recent detections
        </h2>
        <div className="rounded-md border border-slate-800 overflow-hidden">
          <table className="w-full text-xs font-mono">
            <thead className="bg-slate-900 text-slate-400 uppercase tracking-wider">
              <tr>
                <th className="text-left px-3 py-2">Time</th>
                <th className="text-left px-3 py-2">Class</th>
                <th className="text-left px-3 py-2">Severity</th>
                <th className="text-right px-3 py-2">Pos (m)</th>
                <th className="text-right px-3 py-2">Depth (mm)</th>
                <th className="text-right px-3 py-2">Conf</th>
                <th className="text-left px-3 py-2">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {recent.length === 0 && (
                <tr><td className="px-3 py-3 text-slate-500" colSpan={7}>No detections yet…</td></tr>
              )}
              {recent.map((evt, i) => (
                <tr key={i} className="border-t border-slate-800">
                  <td className="px-3 py-1.5 text-slate-400">
                    {new Date((evt.defect?.timestamp ?? Date.now() / 1000) * 1000).toLocaleTimeString()}
                  </td>
                  <td className="px-3 py-1.5">{evt.defect?.cls}</td>
                  <td className="px-3 py-1.5">
                    <span className={`px-1.5 py-0.5 rounded border ${severityColor(evt.defect?.severity)}`}>
                      {evt.defect?.severity}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-right">{evt.defect?.position_m?.toFixed(2)}</td>
                  <td className="px-3 py-1.5 text-right">{evt.defect?.depth_mm?.toFixed(2)}</td>
                  <td className="px-3 py-1.5 text-right">{evt.defect?.confidence?.toFixed(2)}</td>
                  <td className="px-3 py-1.5">{evt.verdict}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
