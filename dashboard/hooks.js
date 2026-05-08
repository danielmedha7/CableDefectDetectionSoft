// ─────────────────────────────────────────────
// Shared React hooks for CableVision dashboard
// ─────────────────────────────────────────────
import { useEffect, useRef, useState, useCallback } from "react";
import axios from "axios";

const API_BASE = import.meta.env?.VITE_API_BASE ?? "http://localhost:8000";
const WS_URL   = import.meta.env?.VITE_WS_URL   ?? "ws://localhost:8000/ws";

export const api = axios.create({ baseURL: API_BASE, timeout: 5000 });

/* ── WebSocket: auto-reconnect, JSON envelope ─────────────────────────── */
export function useWebSocket(url = WS_URL) {
  const [connected, setConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState(null);
  const wsRef = useRef(null);
  const retryRef = useRef(0);

  useEffect(() => {
    let alive = true;
    function connect() {
      const ws = new WebSocket(url);
      wsRef.current = ws;
      ws.onopen = () => { if (alive) { setConnected(true); retryRef.current = 0; } };
      ws.onclose = () => {
        if (!alive) return;
        setConnected(false);
        const delay = Math.min(5000, 500 * Math.pow(2, retryRef.current++));
        setTimeout(connect, delay);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (e) => {
        try { setLastEvent(JSON.parse(e.data)); } catch { /* ignore non-JSON */ }
      };
    }
    connect();
    return () => { alive = false; wsRef.current?.close(); };
  }, [url]);

  const send = useCallback((obj) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(obj));
    }
  }, []);

  return { connected, lastEvent, send };
}

/* ── Detections feed: rolling buffer keyed off the WS event stream ───── */
export function useDetections(limit = 200) {
  const { connected, lastEvent } = useWebSocket();
  const [detections, setDetections] = useState([]);

  useEffect(() => {
    if (!lastEvent || lastEvent.type !== "defect_detected") return;
    setDetections((prev) => {
      const next = [...prev, lastEvent.payload];
      return next.length > limit ? next.slice(-limit) : next;
    });
  }, [lastEvent, limit]);

  return { connected, detections };
}

/* ── Cable profile: per-session list of defects + verdict ─────────────── */
export function useCableProfile(sessionId) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!sessionId) return;
    let alive = true;
    setLoading(true);
    api.get(`/api/sessions/${sessionId}`)
      .then(r => { if (alive) setData(r.data); })
      .catch(e => { if (alive) setError(e); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [sessionId]);

  return { data, error, loading };
}

/* ── Analytics summary (KPIs / trends) ────────────────────────────────── */
export function useAnalytics(rangeHours = 24) {
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    api.get(`/api/analytics/summary?hours=${rangeHours}`)
      .then(r => { if (alive) setSummary(r.data); })
      .catch(e => { if (alive) setError(e); });
    return () => { alive = false; };
  }, [rangeHours]);

  return { summary, error };
}

/* ── Severity → Tailwind colour helper ────────────────────────────────── */
export const severityColor = (sev) => ({
  low:      "text-emerald-400 border-emerald-400/40 bg-emerald-400/10",
  medium:   "text-amber-400 border-amber-400/40 bg-amber-400/10",
  high:     "text-orange-400 border-orange-400/40 bg-orange-400/10",
  critical: "text-rose-400 border-rose-400/40 bg-rose-400/10",
}[sev] ?? "text-slate-300 border-slate-500/30 bg-slate-500/10");

export const verdictColor = (v) => ({
  PASS:    "text-emerald-400 bg-emerald-400/10 border-emerald-400/40",
  FAIL:    "text-rose-400 bg-rose-400/10 border-rose-400/40",
  REVIEW:  "text-amber-400 bg-amber-400/10 border-amber-400/40",
}[v] ?? "text-slate-300 bg-slate-500/10 border-slate-500/40");
