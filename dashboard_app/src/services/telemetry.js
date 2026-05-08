const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

export function getDashboardApiBaseUrl() {
  if (!API_BASE_URL) {
    throw new Error('VITE_API_BASE_URL is not configured.');
  }

  return API_BASE_URL.replace(/\/$/, '');
}

export async function getTelemetrySnapshot() {
  const response = await fetch(`${getDashboardApiBaseUrl()}/dashboard/snapshot`);
  if (!response.ok) {
    throw new Error(`Dashboard API returned ${response.status}`);
  }

  return response.json();
}
