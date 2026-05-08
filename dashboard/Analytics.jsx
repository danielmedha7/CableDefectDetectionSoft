import { useMemo } from 'react';

function normaliseVerdict(value) {
  const text = String(value ?? '').toUpperCase();
  if (text === 'PASS' || text === 'FAIL' || text === 'REVIEW') {
    return text;
  }
  return 'UNKNOWN';
}

function toDefectClass(defect) {
  return String(defect?.cls ?? defect?.type ?? 'unknown');
}

export default function Analytics({ sessions = [] }) {
  const summary = useMemo(() => {
    const counts = { PASS: 0, FAIL: 0, REVIEW: 0, UNKNOWN: 0 };
    const classCounts = {};
    let totalLengthM = 0;
    let totalDefects = 0;

    for (const session of sessions) {
      counts[normaliseVerdict(session.verdict)] += 1;
      totalLengthM += Number(session.cable_length_m ?? session.cableLengthM ?? 0);
      const defects = Array.isArray(session.defects) ? session.defects : [];
      totalDefects += defects.length;
      for (const defect of defects) {
        const key = toDefectClass(defect);
        classCounts[key] = (classCounts[key] ?? 0) + 1;
      }
    }

    const sortedClasses = Object.entries(classCounts).sort((a, b) => b[1] - a[1]).slice(0, 8);
    const passRate = sessions.length ? (counts.PASS / sessions.length) * 100 : 0;

    return {
      counts,
      totalLengthM,
      totalDefects,
      sortedClasses,
      passRate,
    };
  }, [sessions]);

  return (
    <section className="analytics">
      <header>
        <h2>Analytics</h2>
        <p>{sessions.length} sessions</p>
      </header>

      <div className="analytics__grid">
        <article><h3>Pass Rate</h3><p>{summary.passRate.toFixed(1)}%</p></article>
        <article><h3>Total Length</h3><p>{summary.totalLengthM.toFixed(2)} m</p></article>
        <article><h3>Total Defects</h3><p>{summary.totalDefects}</p></article>
      </div>

      <article>
        <h3>Verdict Distribution</h3>
        <ul>
          <li>PASS: {summary.counts.PASS}</li>
          <li>FAIL: {summary.counts.FAIL}</li>
          <li>REVIEW: {summary.counts.REVIEW}</li>
          <li>UNKNOWN: {summary.counts.UNKNOWN}</li>
        </ul>
      </article>

      <article>
        <h3>Top Defect Classes</h3>
        {summary.sortedClasses.length ? (
          <table>
            <thead>
              <tr>
                <th>Class</th>
                <th>Count</th>
              </tr>
            </thead>
            <tbody>
              {summary.sortedClasses.map(([cls, count]) => (
                <tr key={cls}>
                  <td>{cls}</td>
                  <td>{count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p>No defect data yet.</p>
        )}
      </article>
    </section>
  );
}

