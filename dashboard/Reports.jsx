function formatTime(value) {
  if (!value) {
    return '-';
  }
  const numeric = Number(value);
  if (Number.isFinite(numeric)) {
    const ts = numeric > 1e12 ? numeric : numeric * 1000;
    return new Date(ts).toLocaleString();
  }
  return String(value);
}

function verdictText(report) {
  return String(report.verdict ?? report.status ?? 'UNKNOWN').toUpperCase();
}

export default function Reports({
  reports = [],
  loading = false,
  onOpenReport = () => {},
  onDeleteReport = () => {},
}) {
  return (
    <section className="reports">
      <header>
        <h2>Reports</h2>
        <p>{loading ? 'Loading...' : `${reports.length} report entries`}</p>
      </header>

      {reports.length ? (
        <table>
          <thead>
            <tr>
              <th>Session</th>
              <th>Cable</th>
              <th>Spec</th>
              <th>Started</th>
              <th>Verdict</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {reports.map((report) => (
              <tr key={report.id ?? `${report.cable_id}-${report.started_at}`}>
                <td>{report.id ?? '-'}</td>
                <td>{report.cable_id ?? report.cableId ?? '-'}</td>
                <td>{report.cable_spec ?? report.cableSpec ?? '-'}</td>
                <td>{formatTime(report.started_at ?? report.startedAt)}</td>
                <td>{verdictText(report)}</td>
                <td>
                  <button type="button" onClick={() => onOpenReport(report)}>Open</button>
                  <button type="button" onClick={() => onDeleteReport(report)}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>No reports available.</p>
      )}
    </section>
  );
}
