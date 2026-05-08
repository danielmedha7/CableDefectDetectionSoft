// ─────────────────────────────────────────────
// CableProfile.jsx — defect timeline + severity map per session
// ─────────────────────────────────────────────
import React, { useState } from "react";
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, CartesianGrid,
  Tooltip, ResponsiveContainer,
} from "recharts";
import { useCableProfile, severityColor, verdictColor } from "./hooks";

const sevToScore = { low: 1, medium: 2, high: 3, critical: 4 };

export default function CableProfile() {
  const [sessionId, setSessionId] = useState("");
  const [submitted, setSubmitted] = useState("");
  const { data, loading, error } = useCableProfile(submitted);

  const points = (data?.defects ?? []).map(d => ({
    x: d.position_m,
    y: d.angle_deg,
    z: sevToScore[d.severity] ?? 1,
    cls: d.cls,
    severity: d.severity,
    depth: d.depth_mm,
  }));

  return (
    <div className="p-6 text-slate-200 bg-slate-950 min-h-screen">
      <header className="mb-6">
        <h1 className="text-2xl font-bold tracking-wide">Cable Profile</h1>
        <p className="text-slate-400 text-xs">Defect timeline + severity map</p>
      </header>

      <form
        onSubmit={(e) => { e.preventDefault(); setSubmitted(sessionId.trim()); }}
        className="flex gap-2 mb-6"
      >
        <input
          value={sessionId}
          onChange={(e) => setSessionId(e.target.value)}
          placeholder="Session ID, e.g. a1b2c3d4e5f6"
          className="bg-slate-900 border border-slate-700 px-3 py-2 rounded text-sm font-mono w-72"
        />
        <button
          type="submit"
          className="bg-emerald-500/20 border border-emerald-500/40 text-emerald-300 px-4 py-2 rounded text-sm hover:bg-emerald-500/30"
        >
          Load
        </button>
      </form>

      {loading && <p className="text-slate-400 text-sm">Loading…</p>}
      {error && <p className="text-rose-400 text-sm">Failed to load: {String(error)}</p>}

      {data && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
            <Stat label="Cable ID"      value={data.cable_id} />
            <Stat label="Spec"          value={data.cable_spec} />
            <Stat label="Length"        value={`${data.cable_length_m?.toFixed(2) ?? "—"} m`} />
            <Stat
              label="Verdict"
              value={
                <span className={`px-2 py-0.5 rounded border ${verdictColor(data.verdict)}`}>
                  {data.verdict ?? "PENDING"}
                </span>
              }
            />
          </div>

          <section className="bg-slate-900 border border-slate-800 rounded-md p-4 mb-6">
            <h2 className="text-xs uppercase tracking-widest text-slate-400 mb-3">
              Severity map (position × angle)
            </h2>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart>
                  <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
                  <XAxis dataKey="x" name="Position (m)" stroke="#64748b" />
                  <YAxis dataKey="y" name="Angle (°)" domain={[0, 360]} stroke="#64748b" />
                  <ZAxis dataKey="z" range={[40, 240]} />
                  <Tooltip
                    contentStyle={{ background: "#0f172a", border: "1px solid #334155" }}
                    formatter={(value, name, p) => name === "z"
                      ? [p.payload.severity, "severity"]
                      : [value, name]}
                  />
                  <Scatter data={points} fill="#10b981" />
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          </section>

          <section className="rounded-md border border-slate-800 overflow-hidden">
            <table className="w-full text-xs font-mono">
              <thead className="bg-slate-900 text-slate-400 uppercase tracking-wider">
                <tr>
                  <th className="text-left px-3 py-2">#</th>
                  <th className="text-left px-3 py-2">Class</th>
                  <th className="text-left px-3 py-2">Severity</th>
                  <th className="text-right px-3 py-2">Pos (m)</th>
                  <th className="text-right px-3 py-2">Angle (°)</th>
                  <th className="text-right px-3 py-2">Depth (mm)</th>
                  <th className="text-right px-3 py-2">Conf</th>
                </tr>
              </thead>
              <tbody>
                {(data.defects ?? []).map((d, i) => (
                  <tr key={i} className="border-t border-slate-800">
                    <td className="px-3 py-1.5 text-slate-500">{i + 1}</td>
                    <td className="px-3 py-1.5">{d.cls}</td>
                    <td className="px-3 py-1.5">
                      <span className={`px-1.5 py-0.5 rounded border ${severityColor(d.severity)}`}>
                        {d.severity}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 text-right">{d.position_m?.toFixed(2)}</td>
                    <td className="px-3 py-1.5 text-right">{d.angle_deg?.toFixed(1)}</td>
                    <td className="px-3 py-1.5 text-right">{d.depth_mm?.toFixed(2)}</td>
                    <td className="px-3 py-1.5 text-right">{d.confidence?.toFixed(2)}</td>
                  </tr>
                ))}
                {(!data.defects || data.defects.length === 0) && (
                  <tr><td className="px-3 py-3 text-slate-500" colSpan={7}>No defects in this session.</td></tr>
                )}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded p-3">
      <div className="text-[10px] uppercase tracking-widest text-slate-500 mb-1">{label}</div>
      <div className="text-base font-bold">{value}</div>
    </div>
  );
}
