import { useMemo } from 'react';

function normalizeDefect(item, source) {
  const timestamp = Number(item.timestamp ?? item.ts ?? 0);
  return {
    id: String(item.id ?? `${source}-${timestamp}-${item.cls ?? item.type ?? 'defect'}`),
    source,
    timestamp: Number.isFinite(timestamp) ? timestamp : 0,
    cls: String(item.cls ?? item.type ?? 'unknown'),
    severity: String(item.severity ?? 'low'),
    confidence: Number(item.confidence ?? item.cls_confidence ?? 0),
    positionM: Number(item.position_m ?? item.positionM ?? 0),
    depthMm: Number(item.depth_mm ?? item.depthMm ?? 0),
    sizeMm: Number(item.size_mm ?? item.sizeMm ?? 0),
    cameraId: item.camera_id ?? item.cameraId ?? '-',
    channel: item.channel ?? '-',
    verdict: item.verdict ?? '-',
  };
}

function formatTime(value) {
  if (!value) {
    return '-';
  }
  const ts = value > 1e12 ? value : value * 1000;
  return new Date(ts).toLocaleString();
}

export default function InternalDefectLog({
  surfaceDefects = [],
  internalDefects = [],
  maxRows = 200,
}) {
  const rows = useMemo(() => {
    const surface = surfaceDefects.map((item) => normalizeDefect(item, 'surface'));
    const internal = internalDefects.map((item) => normalizeDefect(item, 'internal'));
    return [...surface, ...internal]
      .sort((a, b) => b.timestamp - a.timestamp)
      .slice(0, maxRows);
  }, [surfaceDefects, internalDefects, maxRows]);

  return (
    <section className="internal-defect-log">
      <header>
        <h2>Defect Log</h2>
        <p>{rows.length} recent rows</p>
      </header>

      {rows.length ? (
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Source</th>
              <th>Class</th>
              <th>Severity</th>
              <th>Position (m)</th>
              <th>Depth (mm)</th>
              <th>Size (mm)</th>
              <th>Confidence</th>
              <th>Camera</th>
              <th>Channel</th>
              <th>Verdict</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{formatTime(row.timestamp)}</td>
                <td>{row.source}</td>
                <td>{row.cls}</td>
                <td>{row.severity}</td>
                <td>{row.positionM.toFixed(3)}</td>
                <td>{row.depthMm.toFixed(3)}</td>
                <td>{row.sizeMm.toFixed(3)}</td>
                <td>{row.confidence.toFixed(3)}</td>
                <td>{row.cameraId}</td>
                <td>{row.channel}</td>
                <td>{row.verdict}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>No defects logged.</p>
      )}
    </section>
  );
}

