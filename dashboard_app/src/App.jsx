import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Camera,
  Cpu,
  Gauge,
  Pause,
  Play,
  RadioTower,
  RefreshCcw,
  Ruler,
  ShieldCheck,
  Siren,
} from 'lucide-react';
import { getTelemetrySnapshot } from './services/telemetry.js';

const severityRank = {
  low: 1,
  medium: 2,
  high: 3,
};

function classNames(...items) {
  return items.filter(Boolean).join(' ');
}

function formatDelta(value) {
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(3)} mm`;
}

function StatusPill({ status }) {
  const normalized = status.toLowerCase();
  return <span className={classNames('status-pill', `status-pill--${normalized}`)}>{status}</span>;
}

function MetricCard({ icon: Icon, label, value, helper, tone = 'neutral' }) {
  return (
    <section className={classNames('metric-card', `metric-card--${tone}`)}>
      <div className="metric-card__icon" aria-hidden="true">
        <Icon size={18} />
      </div>
      <div>
        <p>{label}</p>
        <strong>{value}</strong>
        <span>{helper}</span>
      </div>
    </section>
  );
}

function DeviceStatus({ telemetry }) {
  const devices = [
    { label: 'Jetson', detail: `${telemetry.devices.jetson.load}% load`, status: telemetry.devices.jetson.status },
    { label: 'Pi A', detail: `${telemetry.devices.piA.latencyMs} ms link`, status: telemetry.devices.piA.status },
    { label: 'Pi B', detail: `${telemetry.devices.piB.latencyMs} ms link`, status: telemetry.devices.piB.status },
  ];

  return (
    <section className="panel device-panel">
      <div className="panel__heading">
        <div>
          <p className="eyebrow">Hardware</p>
          <h2>Device Chain</h2>
        </div>
        <Cpu size={20} />
      </div>
      <div className="device-list">
        {devices.map((device) => (
          <div className="device-row" key={device.label}>
            <span className="device-row__dot" aria-hidden="true" />
            <div>
              <strong>{device.label}</strong>
              <p>{device.detail}</p>
            </div>
            <StatusPill status={device.status} />
          </div>
        ))}
      </div>
    </section>
  );
}

function CameraFeed({ camera, index }) {
  const previewUrl = camera.previewUrl || camera.streamUrl || camera.imageUrl;

  return (
    <section className="camera-card">
      <div className="camera-card__top">
        <div>
          <p>{camera.id}</p>
          <h3>{camera.name}</h3>
        </div>
        <StatusPill status={camera.laserLocked ? 'Locked' : 'Drift'} />
      </div>
      <div className="camera-viewport" style={{ '--phase': `${index * 18}px` }}>
        {previewUrl ? (
          <img src={previewUrl} alt={`${camera.id} live preview`} />
        ) : (
          <span className="camera-viewport__empty">No preview feed</span>
        )}
      </div>
      <div className="camera-stats">
        <span>{camera.fps} fps</span>
        <span>{camera.exposureMs} ms</span>
        <span>{camera.signal}% signal</span>
        <span>{camera.frameAgeMs} ms age</span>
      </div>
    </section>
  );
}

function ProfileChart({ profile = [] }) {
  if (!profile.length) {
    return (
      <section className="panel profile-panel">
        <div className="panel__heading">
          <div>
            <p className="eyebrow">Measurement</p>
            <h2>Cable Diameter Profile</h2>
          </div>
          <Ruler size={20} />
        </div>
        <div className="empty-panel">Waiting for measurement profile.</div>
      </section>
    );
  }

  const values = profile.map((point) => point.diameterMm);
  const min = Math.min(...values) - 0.1;
  const max = Math.max(...values) + 0.1;
  const range = Math.max(max - min, 0.001);

  return (
    <section className="panel profile-panel">
      <div className="panel__heading">
        <div>
          <p className="eyebrow">Measurement</p>
          <h2>Cable Diameter Profile</h2>
        </div>
        <Ruler size={20} />
      </div>
      <div className="profile-chart" role="img" aria-label="Cable diameter profile over the inspected section">
        {profile.map((point) => {
          const height = 22 + ((point.diameterMm - min) / range) * 68;
          const inTolerance = Math.abs(point.diameterMm - 32) <= 0.35;
          return (
            <span
              className={classNames('profile-chart__bar', !inTolerance && 'profile-chart__bar--alert')}
              key={point.positionMm}
              style={{ height: `${height}%` }}
              title={`${point.positionMm} mm: ${point.diameterMm} mm`}
            />
          );
        })}
      </div>
      <div className="profile-panel__scale">
        <span>{min.toFixed(2)} mm</span>
        <span>Target 32.00 mm</span>
        <span>{max.toFixed(2)} mm</span>
      </div>
    </section>
  );
}

function DefectTable({ defects = [] }) {
  const sortedDefects = useMemo(
    () => [...defects].sort((a, b) => severityRank[b.severity] - severityRank[a.severity]),
    [defects],
  );

  return (
    <section className="panel defects-panel">
      <div className="panel__heading">
        <div>
          <p className="eyebrow">Inspection</p>
          <h2>Defect Log</h2>
        </div>
        <Siren size={20} />
      </div>
      <div className="defect-table" role="table" aria-label="Detected defects">
        <div className="defect-table__row defect-table__row--head" role="row">
          <span>Defect</span>
          <span>Position</span>
          <span>Source</span>
          <span>Confidence</span>
          <span>Status</span>
        </div>
        {sortedDefects.length ? (
          sortedDefects.map((defect) => (
            <div className="defect-table__row" role="row" key={defect.id}>
              <span>
                <strong>{defect.type}</strong>
                <em className={`severity severity--${defect.severity}`}>{defect.severity}</em>
              </span>
              <span>{defect.positionMm} mm</span>
              <span>{defect.camera}</span>
              <span>{defect.confidence}%</span>
              <span>{defect.status}</span>
            </div>
          ))
        ) : (
          <div className="empty-panel">No defects reported by the backend.</div>
        )}
      </div>
    </section>
  );
}

function App() {
  const [telemetry, setTelemetry] = useState(null);
  const [isPaused, setIsPaused] = useState(false);
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(true);

  const refreshSnapshot = useCallback(async () => {
    try {
      const snapshot = await getTelemetrySnapshot();
      setTelemetry(snapshot);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load telemetry snapshot.');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isPaused) {
      return undefined;
    }

    let active = true;

    async function refresh() {
      if (active) {
        await refreshSnapshot();
      }
    }

    refresh();
    const timerId = window.setInterval(refresh, 1400);
    return () => {
      active = false;
      window.clearInterval(timerId);
    };
  }, [isPaused, refreshSnapshot]);

  if (!telemetry) {
    return (
      <main className="app-shell">
        <header className="topbar">
          <div>
            <p className="eyebrow">CableVision Prototype</p>
            <h1>High Voltage Cable Deficiency Monitor</h1>
          </div>
          <div className="topbar__actions">
            <button className="icon-button" type="button" onClick={refreshSnapshot} title="Refresh snapshot">
              <RefreshCcw size={18} />
            </button>
          </div>
        </header>

        <section className="panel connection-panel">
          <div className="panel__heading">
            <div>
              <p className="eyebrow">Backend</p>
              <h2>{isLoading ? 'Connecting to Jetson API' : 'Telemetry unavailable'}</h2>
            </div>
            <AlertTriangle size={20} />
          </div>
          <p>{error || 'Waiting for the first dashboard snapshot.'}</p>
        </section>
      </main>
    );
  }

  const diameterDelta = telemetry.measurements.diameterMm - telemetry.measurements.targetDiameterMm;
  const diameterTone = Math.abs(diameterDelta) > telemetry.measurements.toleranceMm ? 'danger' : 'good';
  const roundnessTone = telemetry.measurements.roundnessMm > 0.13 ? 'warning' : 'good';
  const riskTone = telemetry.measurements.internalRisk > 22 ? 'warning' : 'neutral';

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">CableVision Prototype</p>
          <h1>High Voltage Cable Deficiency Monitor</h1>
        </div>
        <div className="topbar__actions">
          <button className="icon-button" type="button" onClick={refreshSnapshot} title="Refresh snapshot">
            <RefreshCcw size={18} />
          </button>
          <button className="control-button" type="button" onClick={() => setIsPaused((current) => !current)}>
            {isPaused ? <Play size={18} /> : <Pause size={18} />}
            {isPaused ? 'Resume' : 'Pause'}
          </button>
        </div>
      </header>

      <section className="run-strip" aria-label="Current run status">
        <div>
          <span>Session</span>
          <strong>{telemetry.session.id}</strong>
        </div>
        <div>
          <span>Status</span>
          <strong>{telemetry.session.status}</strong>
        </div>
        <div>
          <span>Length</span>
          <strong>{telemetry.session.inspectedLengthM} m</strong>
        </div>
        <div>
          <span>Line Speed</span>
          <strong>{telemetry.session.lineSpeedMMin} m/min</strong>
        </div>
        <div>
          <span>Updated</span>
          <strong>{telemetry.updatedAt}</strong>
        </div>
      </section>

      {error && (
        <section className="notice" role="status">
          <AlertTriangle size={18} />
          <span>{error}</span>
        </section>
      )}

      <section className="metric-grid">
        <MetricCard
          icon={Gauge}
          label="Diameter"
          value={`${telemetry.measurements.diameterMm.toFixed(3)} mm`}
          helper={formatDelta(diameterDelta)}
          tone={diameterTone}
        />
        <MetricCard
          icon={Activity}
          label="Roundness Error"
          value={`${telemetry.measurements.roundnessMm.toFixed(3)} mm`}
          helper="Live cross-section estimate"
          tone={roundnessTone}
        />
        <MetricCard
          icon={ShieldCheck}
          label="Surface Score"
          value={`${telemetry.measurements.surfaceScore}%`}
          helper="Visual model confidence"
          tone="good"
        />
        <MetricCard
          icon={RadioTower}
          label="Internal Risk"
          value={`${telemetry.measurements.internalRisk}%`}
          helper="Ultrasonic model score"
          tone={riskTone}
        />
      </section>

      <section className="workspace-grid">
        <section className="camera-grid" aria-label="Camera feeds">
          {(telemetry.cameras || []).map((camera, index) => (
            <CameraFeed camera={camera} index={index} key={camera.id} />
          ))}
        </section>

        <aside className="side-stack">
          <section className="panel overview-panel">
            <div className="panel__heading">
              <div>
                <p className="eyebrow">Run Setup</p>
                <h2>{telemetry.session.cableType}</h2>
              </div>
              <Camera size={20} />
            </div>
            <dl className="overview-list">
              <div>
                <dt>Operator</dt>
                <dd>{telemetry.session.operator}</dd>
              </div>
              <div>
                <dt>Started</dt>
                <dd>{telemetry.session.startedAt}</dd>
              </div>
              <div>
                <dt>Data Source</dt>
                <dd>Jetson API</dd>
              </div>
            </dl>
          </section>
          <DeviceStatus telemetry={telemetry} />
        </aside>
      </section>

      <section className="lower-grid">
        <ProfileChart profile={telemetry.profile || []} />
        <DefectTable defects={telemetry.defects || []} />
      </section>
    </main>
  );
}

export default App;
