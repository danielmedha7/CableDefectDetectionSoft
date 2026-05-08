import { useMemo } from 'react';

function toProfilePoint(item, index) {
  const position = Number(item.position_mm ?? item.positionMm ?? index);
  const diameter = Number(item.diameter_mm ?? item.diameterMm ?? 0);
  const roundness = Number(item.roundness_error_mm ?? item.roundnessMm ?? 0);
  return {
    position: Number.isFinite(position) ? position : index,
    diameter: Number.isFinite(diameter) ? diameter : 0,
    roundness: Number.isFinite(roundness) ? roundness : 0,
  };
}

function buildPath(points, minX, maxX, minY, maxY, width, height) {
  const spanX = Math.max(maxX - minX, 1e-9);
  const spanY = Math.max(maxY - minY, 1e-9);
  return points
    .map((point, index) => {
      const x = ((point.position - minX) / spanX) * width;
      const y = height - ((point.diameter - minY) / spanY) * height;
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(' ');
}

export default function CableProfile({
  profilePoints = [],
  targetDiameterMm = 0,
  toleranceMm = 0,
  roundnessLimitMm = 0,
}) {
  const points = useMemo(() => profilePoints.map(toProfilePoint), [profilePoints]);
  const stats = useMemo(() => {
    if (!points.length) {
      return null;
    }
    const diameters = points.map((point) => point.diameter);
    const roundnessValues = points.map((point) => point.roundness);
    return {
      minDiameter: Math.min(...diameters),
      maxDiameter: Math.max(...diameters),
      maxRoundness: Math.max(...roundnessValues),
      outOfToleranceCount: points.filter(
        (point) => Math.abs(point.diameter - targetDiameterMm) > toleranceMm,
      ).length,
    };
  }, [points, targetDiameterMm, toleranceMm]);

  if (!points.length) {
    return (
      <section className="cable-profile">
        <h2>Cable Profile</h2>
        <p>No diameter profile received yet.</p>
      </section>
    );
  }

  const minX = points[0].position;
  const maxX = points[points.length - 1].position;
  const minY = Math.min(...points.map((point) => point.diameter), targetDiameterMm - toleranceMm);
  const maxY = Math.max(...points.map((point) => point.diameter), targetDiameterMm + toleranceMm);
  const width = 1000;
  const height = 260;
  const profilePath = buildPath(points, minX, maxX, minY, maxY, width, height);

  return (
    <section className="cable-profile">
      <header>
        <h2>Cable Profile</h2>
        <p>Target {targetDiameterMm.toFixed(3)} mm ± {toleranceMm.toFixed(3)} mm</p>
      </header>

      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Diameter profile">
        <path d={profilePath} fill="none" stroke="currentColor" strokeWidth="2" />
      </svg>

      <dl className="cable-profile__stats">
        <div><dt>Min Diameter</dt><dd>{stats.minDiameter.toFixed(3)} mm</dd></div>
        <div><dt>Max Diameter</dt><dd>{stats.maxDiameter.toFixed(3)} mm</dd></div>
        <div><dt>Max Roundness Error</dt><dd>{stats.maxRoundness.toFixed(3)} mm</dd></div>
        <div><dt>Out of Tolerance Points</dt><dd>{stats.outOfToleranceCount}</dd></div>
        <div><dt>Roundness Limit</dt><dd>{roundnessLimitMm.toFixed(3)} mm</dd></div>
      </dl>
    </section>
  );
}

