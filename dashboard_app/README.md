# CableVision Dashboard

React dashboard for the high voltage cable deficiency tester prototype.

## Configure API

Create `.env.local`:

```powershell
VITE_API_BASE_URL=http://JETSON_IP_ADDRESS:8000
```

The dashboard requests:

```text
GET /dashboard/snapshot
```

The frontend does not generate telemetry. If the API is unavailable, it shows a connection error.

## Run

```powershell
npm install
npm run dev
```

Open `http://127.0.0.1:5173/`.
