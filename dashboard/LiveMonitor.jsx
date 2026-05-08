import { useMemo } from 'react';

function asNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function asString(value, fallback = '-') {
  return typeof value === 'string' && value.length ? value : fallback;
}

function formatTime(value) {
  if (value == null) {
    return '-';
  }
  const parsed = Number(value);
  if (Number.isFinite(parsed)) {
    const ts = parsed > 1e12 ? parsed : parsed * 1000;
    return new Date(ts).toLocaleString();
  }
  if (typeof value === 'string') {
    return value;
  }
  return '-';
}

function getSession(snapshot) {
  const session = snapshot?.session ?? {};
  return {
    id: asString(session.id),
    cableId: asString(session.cable_id ?? session.cableId),
    cableSpec: asString(session.cable_spec ?? session.cableSpec),
    operator: asString(session.operator),
    startedAt: formatTime(session.started_at ?? session.startedAt),
    status: asString(session.status ?? session.verdict ?? 'RUNNING'),
  };
}

function getPosition(snapshot) {
  const position = snapshot?.position ?? {};
  return {
    positionM: asNumber(position.position_m ?? position.positionM),
    speedMps: asNumber(position.speed_mps ?? position.speedMps),
  };
}

function getMeasurements(snapshot) {
  const m = snapshot?.measurements ?? {};
  return {
    diameterMm: asNumber(m.diameter_mm ?? m.diameterMm),
    targetDiameterMm: asNumber(m.target_diameter_mm ?? m.targetDiameterMm),
    roundnessErrorMm: asNumber(m.roundness_error_mm ?? m.roundnessErrorMm),
    surfaceScore: asNumber(m.surface_score ?? m.surfaceScore),
    internalRisk: asNumber(m.internal_risk ?? m.internalRisk),
  };
}

function getDevices(snapshot) {
  if (Array.isArray(snapshot?.devices)) {
    return snapshot.devices;
  }
  const devices = snapshot?.devices ?? {};
  return [
    { id: 'jetson', ...devices.jetson },
    { id: 'pi_a', ...devices.pi_a, name: 'Pi A' },
    { id: 'pi_b', ...devices.pi_b, name: 'Pi B' },
  ].filter((item) => Object.keys(item).length > 1);
}

export default function LiveMonitor({
  snapshot,
  loading = false,
  paused = false,
  error = '',
  onRefresh = () => {},
  onTogglePause = () => {},
}) {
  const session = useMemo(() => getSession(snapshot), [snapshot]);
  const position = useMemo(() => getPosition(snapshot), [snapshot]);
  const measurements = useMemo(() => getMeasurements(snapshot), [snapshot]);
  const devices = useMemo(() => getDevices(snapshot), [snapshot]);

  return (
    <section className="live-monitor">
      <header className="live-monitor__header">
        <div>
          <h2>Live Monitor</h2>
          <p>Session {session.id} · {session.status}</p>
        </div>
        <div className="live-monitor__controls">
          <button type="button" onClick={onRefresh} disabled={loading}>Refresh</button>
          <button type="button" onClick={onTogglePause}>{paused ? 'Resume' : 'Pause'}</button>
        </div>
      </header>

      {error ? <p className="live-monitor__error">{error}</p> : null}

      <div className="live-monitor__grid">
        <article>
          <h3>Run</h3>
          <dl>
            <div><dt>Cable</dt><dd>{session.cableId}</dd></div>
            <div><dt>Spec</dt><dd>{session.cableSpec}</dd></div>
            <div><dt>Operator</dt><dd>{session.operator}</dd></div>
            <div><dt>Started</dt><dd>{session.startedAt}</dd></div>
          </dl>
        </article>

        <article>
          <h3>Position</h3>
          <dl>
            <div><dt>Length</dt><dd>{position.positionM.toFixed(3)} m</dd></div>
            <div><dt>Speed</dt><dd>{position.speedMps.toFixed(3)} m/s</dd></div>
          </dl>
        </article>

        <article>
          <h3>Measurements</h3>
          <dl>
            <div><dt>Diameter</dt><dd>{measurements.diameterMm.toFixed(3)} mm</dd></div>
            <div><dt>Target</dt><dd>{measurements.targetDiameterMm.toFixed(3)} mm</dd></div>
            <div><dt>Roundness Error</dt><dd>{measurements.roundnessErrorMm.toFixed(3)} mm</dd></div>
            <div><dt>Surface Score</dt><dd>{measurements.surfaceScore.toFixed(1)}%</dd></div>
            <div><dt>Internal Risk</dt><dd>{measurements.internalRisk.toFixed(1)}%</dd></div>
          </dl>
        </article>
      </div>

      <article className="live-monitor__devices">
        <h3>Device Chain</h3>
        {devices.length ? (
          <table>
            <thead>
              <tr>
                <th>Device</th>
                <th>Status</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {devices.map((device) => (
                <tr key={device.id ?? device.name}>
                  <td>{asString(device.name ?? device.id)}</td>
                  <td>{asString(device.status, 'unknown')}</td>
                  <td>{JSON.stringify(device)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p>No device telemetry.</p>
        )}
      </article>
    </section>
  );
}

