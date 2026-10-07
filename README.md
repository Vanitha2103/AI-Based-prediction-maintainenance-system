# MotorWatch — Full-stack motor maintenance demo

MotorWatch is a local full-stack prototype for viewing industrial electric-motor condition data. The frontend is plain HTML, CSS, and JavaScript. A Python standard-library HTTP server supplies a JSON API, persists motor readings and maintenance events in SQLite, and simulates incoming sensor measurements.

## Start the application on Windows

1. Install Python 3.9 or later if it is not already installed.
2. From this folder, double-click `run.bat`, or open PowerShell here and run:

   ```powershell
   py backend.py
   ```

3. Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser. Do not open `index.html` directly or use the old `file:///.../index.html` browser tab. Keep the terminal open while using the app; press Ctrl+C to stop the server.

The dashboard and backend need to be opened through the server; opening `index.html` directly will not connect to the API. No Python packages or frontend build tools are required. Application data is stored in `data/motorwatch.db`, created automatically when the server starts. Set `MOTORWATCH_DB` to use a different SQLite file.

## Backend API

- `GET /api/health` — service health check.
- `GET /api/state` — current motor fleet, summary counts, active events, and simulation state.
- `GET /api/motors/{motor_id}/history` — latest 30 stored condition readings for a motor.
- `GET /api/export.csv` — download a CSV report generated from the current server data.
- `POST /api/control/pause` — pause or resume simulation with `{"paused": true}` or `{"paused": false}`.
- `POST /api/control/source` — select `{"source": "simulation"}` or `{"source": "external"}` as the telemetry source.
- `POST /api/events/{event_id}/ack` — acknowledge an active maintenance event.
- `POST /api/telemetry/{motor_id}` — ingest readings, for example `{"temperature": 74.2, "vibration": 3.1, "current": 18.0, "speed": 1470}`.

Telemetry ingestion validates numeric values and bounds, stores each accepted sample, and updates the motor health/state. The first accepted ingestion automatically switches the source to `external`, preventing the simulator from overwriting incoming readings; use `/api/control/source` to switch sources again. Unknown motors and invalid requests return explicit HTTP errors. The demo UI polls the API every two seconds; source, pause state, and acknowledgements are shared by all browser tabs because they are stored by the backend.

## Safety and production readiness

The bundled readings are simulated. Although the ingestion route accepts readings from another local client, this demo does not connect to a physical sensor, PLC, SCADA system, MQTT broker, or OPC UA server. Its weighted health score, thresholds, history, and maintenance suggestions are illustrative rules—not a trained or validated predictive model. Do not use the application to control machinery or make real maintenance or safety decisions.

The server binds to `127.0.0.1` by default and has no user authentication. Keep it local and do not expose it to a plant network or the public internet. A production deployment needs authenticated and encrypted access, authorization, audit logging, hardened industrial gateway/protocol integration, validated device identity and timestamps, monitoring/backup, and a predictive model trained and verified against relevant operating and failure data. Validate all thresholds and alerts with qualified maintenance and safety engineers before operational use.
