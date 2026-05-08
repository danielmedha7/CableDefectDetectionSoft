// ─────────────────────────────────────────────
// InternalDefectLog.jsx — ultrasonic internal defects
// ─────────────────────────────────────────────
import React, { useEffect, useState, useMemo } from "react";
import { api, useWebSocket, severityColor } from "./hooks";

export default function InternalDefectLog() {
  const [items, setItems] = useState([]);
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const { lastEvent } = useWebSocket();

  useEffect(() => {
    let alive = true;
    api.get("/api/defects/internal?limit=200")
      .then(r => { if (alive) setItems(r.data ?? []); })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (lastEvent?.type !== "internal_defect") return;
    setItems(prev => [lastEvent.payload, ...prev].slice(0, 500));
  }, [lastEvent]);

  const filtered = useMemo(() => {
    if (filter === "all") return items;
    return items.filter(d => d.cls === filter);
  }, [items, filter]);

  const classes = useMemo(
    () => Array.from(new Set(items.map(d => d.cls))).sort(),
    [items],
  );

  return (
    <div className="p-6 text-slate-200 bg-slate-950 min-h-screen">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-wide">Internal Defect Log</h1>
          <p className="text-slate-400 text-xs">Ultrasonic A-scan classifications · XGBoost</p>
        </div>
        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-sm font-mono"
        >
          <option value="all">all classes</option>
          {classes.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </header>

      {loading && <p className="text-slate-400 text-sm">Loading…</p>}

      <div className="rounded-md border border-slate-800 overflow-hidden">
        <table className="w-full text-xs font-mono">
          <thead className="bg-slate-900 text-slate-400 uppercase tracking-wider">
            <tr>
              <th className="text-left px-3 py-2">Time</th>
              <th className="text-left px-3 py-2">Class</th>
              <th className="text-left px-3 py-2">Severity</th>
              <th className="text-right px-3 py-2">Conf</th>
              <th className="text-right px-3 py-2">Depth (mm)</th>
              <th className="text-right px-3 py-2">Size (mm)</th>
              <th className="text-right px-3 py-2">Pos (m)</th>
              <th className="text-right px-3 py-2">Channel</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((d, i) => (
              <tr key={i} className="border-t border-slate-800">
                <td className="px-3 py-1.5 text-slate-400">
                  {new Date((d.timestamp ?? Date.now() / 1000) * 1000).toLocaleTimeString()}
                </td>
                <td className="px-3 py-1.5">{d.cls}</td>
                <td className="px-3 py-1.5">
                  <span className={`px-1.5 py-0.5 rounded border ${severityColor(d.severity)}`}>
                    {d.severity ?? "—"}
                  </span>
                </td>
                <td className="px-3 py-1.5 text-right">{d.confidence?.toFixed(2)}</td>
                <td className="px-3 py-1.5 text-right">{d.depth_mm?.toFixed(2)}</td>
                <td className="px-3 py-1.5 text-right">{d.size_mm?.toFixed(2)}</td>
                <td className="px-3 py-1.5 text-right">{d.position_m?.toFixed(2)}</td>
                <td className="px-3 py-1.5 text-right">{d.channel}</td>
              </tr>
            ))}
            {filtered.length === 0 && !loading && (
              <tr><td colSpan={8} className="px-3 py-3 text-slate-500">No internal defects logged.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
