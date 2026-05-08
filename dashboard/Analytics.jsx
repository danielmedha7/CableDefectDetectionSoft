// ─────────────────────────────────────────────
// Analytics.jsx — KPIs + trend charts
// ─────────────────────────────────────────────
import React, { useState } from "react";
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, PieChart, Pie, Cell, Legend,
} from "recharts";
import { useAnalytics } from "./hooks";

const PIE_COLORS = ["#10b981", "#fbbf24", "#fb923c", "#fb7185", "#a78bfa", "#22d3ee"];

export default function Analytics() {
  const [range, setRange] = useState(24);
  const { summary, error } = useAnalytics(range);

  const kpi = summary?.kpis ?? {};
  const byClass = summary?.by_class ?? [];
  const verdictPie = summary?.verdicts ?? [];
  const trend = summary?.trend ?? [];

  return (
    <div className="p-6 text-slate-200 bg-slate-950 min-h-screen">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-wide">Analytics</h1>
          <p className="text-slate-400 text-xs">Shift KPIs and defect trends</p>
        </div>
        <select
          value={range}
          onChange={(e) => setRange(parseInt(e.target.value, 10))}
          className="bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-sm font-mono"
        >
          <option value={1}>last 1 h</option>
          <option value={8}>last 8 h</option>
          <option value={24}>last 24 h</option>
          <option value={168}>last 7 d</option>
        </select>
      </header>

      {error && <p className="text-rose-400 text-sm mb-4">{String(error)}</p>}

      <section className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        <Kpi label="Sessions"     value={kpi.sessions ?? 0} />
        <Kpi label="Total defects" value={kpi.defects ?? 0} />
        <Kpi label="Pass rate"    value={kpi.pass_rate != null ? `${(kpi.pass_rate * 100).toFixed(1)}%` : "—"} />
        <Kpi label="Avg length"   value={kpi.avg_length_m != null ? `${kpi.avg_length_m.toFixed(1)} m` : "—"} />
      </section>

      <section className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        <Panel title="Defects by class">
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={byClass}>
                <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
                <XAxis dataKey="cls" stroke="#64748b" />
                <YAxis stroke="#64748b" />
                <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} />
                <Bar dataKey="count" fill="#10b981" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel title="Verdicts">
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={verdictPie} dataKey="count" nameKey="verdict" outerRadius={90} label>
                  {verdictPie.map((_, i) => (
                    <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <Legend />
                <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      </section>

      <Panel title="Defects over time">
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={trend}>
              <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
              <XAxis dataKey="bucket" stroke="#64748b" />
              <YAxis stroke="#64748b" />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} />
              <Line type="monotone" dataKey="count" stroke="#22d3ee" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </Panel>
    </div>
  );
}

function Kpi({ label, value }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded p-3">
      <div className="text-[10px] uppercase tracking-widest text-slate-500 mb-1">{label}</div>
      <div className="text-2xl font-bold">{value}</div>
    </div>
  );
}
function Panel({ title, children }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-md p-4">
      <h2 className="text-xs uppercase tracking-widest text-slate-400 mb-3">{title}</h2>
      {children}
    </div>
  );
}
