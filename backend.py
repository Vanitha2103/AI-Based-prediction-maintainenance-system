"""Local MotorWatch API, SQLite store, and simulated motor telemetry."""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import math
import mimetypes
import os
import random
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("MOTORWATCH_DB", ROOT / "data" / "motorwatch.db"))
TICK_SECONDS = 2
MAX_REQUEST_BYTES = 16_384
LOG = logging.getLogger("motorwatch")
DB_LOCK = threading.RLock()
_connection: sqlite3.Connection | None = None
_simulator_stop = threading.Event()
_simulator_thread: threading.Thread | None = None

SEED_MOTORS = (
    ("M-204", "Compressor line · Unit 04", 76, 78.4, 4.2, 18.6, 1450),
    ("M-107", "Cooling system · Unit 02", 94, 62.1, 2.1, 14.8, 1482),
    ("M-311", "Conveyor line · Unit 11", 88, 70.6, 3.1, 17.2, 1468),
    ("M-208", "Pump station · Unit 08", 58, 91.7, 6.8, 21.4, 1398),
    ("M-115", "Ventilation · Unit 05", 91, 66.3, 2.6, 15.5, 1491),
    ("M-401", "Mixer line · Unit 01", 72, 81.2, 4.8, 19.6, 1436),
    ("M-222", "Packaging · Unit 06", 96, 59.8, 1.8, 13.9, 1496),
    ("M-309", "Conveyor line · Unit 09", 89, 68.5, 2.9, 16.3, 1470),
)

SEED_EVENTS = (
    ("evt-1", "M-208", "critical", "Abnormal vibration detected",
     "Vibration exceeds the 4.5 mm/s limit", "Inspect bearing and alignment", 4),
    ("evt-2", "M-401", "watch", "Temperature approaching limit",
     "Operating above the normal temperature range", "Check cooling and ventilation", 18),
    ("evt-3", "M-204", "watch", "Health score trending downward",
     "Condition indicators need review", "Schedule a routine inspection", 36),
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="seconds")


def sensor_risk(motor: dict) -> int:
    temperature_risk = max(0.0, (motor["temperature"] - 65) / 25)
    vibration_risk = max(0.0, (motor["vibration"] - 2) / 5)
    current_risk = max(0.0, (motor["current"] - 15) / 10)
    speed_risk = max(0.0, (1450 - motor["speed"]) / 200)
    return min(100, round(
        temperature_risk * 30 + vibration_risk * 40
        + current_risk * 15 + speed_risk * 15
    ))


def health_state(health: int) -> str:
    if health < 65:
        return "critical"
    if health < 82:
        return "watch"
    return "healthy"


def get_connection() -> sqlite3.Connection:
    global _connection
    with DB_LOCK:
        if _connection is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _connection = sqlite3.connect(DB_PATH, check_same_thread=False)
            _connection.row_factory = sqlite3.Row
            _connection.execute("PRAGMA foreign_keys = ON")
            _connection.execute("PRAGMA journal_mode = WAL")
        return _connection


def initialize_database() -> None:
    connection = get_connection()
    with DB_LOCK:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS motors (
                id TEXT PRIMARY KEY,
                location TEXT NOT NULL,
                health INTEGER NOT NULL,
                temperature REAL NOT NULL,
                vibration REAL NOT NULL,
                current REAL NOT NULL,
                speed INTEGER NOT NULL,
                state TEXT NOT NULL,
                baseline_health INTEGER NOT NULL,
                baseline_sensor_risk INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                motor_id TEXT NOT NULL REFERENCES motors(id),
                timestamp TEXT NOT NULL,
                health INTEGER NOT NULL,
                temperature REAL NOT NULL,
                vibration REAL NOT NULL,
                current REAL NOT NULL,
                speed INTEGER NOT NULL,
                state TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS telemetry_motor_time
                ON telemetry(motor_id, timestamp DESC);
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                motor_id TEXT NOT NULL REFERENCES motors(id),
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT NOT NULL,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL,
                acknowledged_at TEXT
            );
            CREATE INDEX IF NOT EXISTS events_created
                ON events(created_at DESC);
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        if connection.execute("SELECT COUNT(*) FROM motors").fetchone()[0] == 0:
            now = utc_now()
            for motor_id, location, health, temperature, vibration, current, speed in SEED_MOTORS:
                motor = {
                    "temperature": temperature, "vibration": vibration,
                    "current": current, "speed": speed,
                }
                baseline_risk = sensor_risk(motor)
                state = health_state(health)
                connection.execute(
                    """INSERT INTO motors
                       (id, location, health, temperature, vibration, current, speed,
                        state, baseline_health, baseline_sensor_risk, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (motor_id, location, health, temperature, vibration, current,
                     speed, state, health, baseline_risk, timestamp(now)),
                )
                for age in range(29, -1, -1):
                    historic_health = max(35, min(99, health + round(math.sin(age * 0.43) * 1.3)))
                    connection.execute(
                        """INSERT INTO telemetry
                           (motor_id, timestamp, health, temperature, vibration,
                            current, speed, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (motor_id, timestamp(now - timedelta(minutes=age)),
                         historic_health, temperature, vibration, current, speed,
                         health_state(historic_health)),
                    )
            for event_id, motor_id, severity, title, detail, action, age_minutes in SEED_EVENTS:
                connection.execute(
                    """INSERT INTO events
                       (id, motor_id, severity, title, detail, action, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (event_id, motor_id, severity, title, detail, action,
                     timestamp(now - timedelta(minutes=age_minutes))),
                )
            connection.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('paused', 'false')"
            )
            connection.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('data_source', 'simulation')"
            )
        else:
            connection.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('paused', 'false')"
            )
            connection.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('data_source', 'simulation')"
            )
        connection.commit()


def setting_is_paused() -> bool:
    with DB_LOCK:
        row = get_connection().execute(
            "SELECT value FROM settings WHERE key = 'paused'"
        ).fetchone()
    return bool(row and row["value"] == "true")


def set_paused(paused: bool) -> None:
    with DB_LOCK:
        connection = get_connection()
        connection.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('paused', ?)",
            ("true" if paused else "false",),
        )
        connection.commit()


def data_source() -> str:
    with DB_LOCK:
        row = get_connection().execute(
            "SELECT value FROM settings WHERE key = 'data_source'"
        ).fetchone()
    return row["value"] if row else "simulation"


def set_data_source(source: str) -> None:
    with DB_LOCK:
        connection = get_connection()
        connection.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('data_source', ?)",
            (source,),
        )
        connection.commit()


def rows_as_dicts(query: str, parameters: tuple = ()) -> list[dict]:
    with DB_LOCK:
        return [dict(row) for row in get_connection().execute(query, parameters).fetchall()]


def record_telemetry(motor_id: str, readings: dict, *, source: str) -> dict | None:
    with DB_LOCK:
        connection = get_connection()
        row = connection.execute("SELECT * FROM motors WHERE id = ?", (motor_id,)).fetchone()
        if row is None:
            return None
        previous_state = row["state"]
        motor = dict(row)
        motor.update(readings)
        health = max(
            35,
            min(99, round(
                motor["baseline_health"]
                - (sensor_risk(motor) - motor["baseline_sensor_risk"])
            )),
        )
        state = health_state(health)
        now = timestamp()
        connection.execute(
            """UPDATE motors SET health=?, temperature=?, vibration=?, current=?,
               speed=?, state=?, updated_at=? WHERE id=?""",
            (health, motor["temperature"], motor["vibration"], motor["current"],
             motor["speed"], state, now, motor_id),
        )
        connection.execute(
            """INSERT INTO telemetry
               (motor_id, timestamp, health, temperature, vibration, current, speed, state)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (motor_id, now, health, motor["temperature"], motor["vibration"],
             motor["current"], motor["speed"], state),
        )
        if state != "healthy" and (state != previous_state or source == "ingest"):
            active = connection.execute(
                "SELECT 1 FROM events WHERE motor_id=? AND acknowledged_at IS NULL",
                (motor_id,),
            ).fetchone()
            if active is None:
                if motor["vibration"] > 4.5:
                    title = "Abnormal vibration detected"
                    detail = f"Vibration is {motor['vibration']:.1f} mm/s; inspect the 4.5 mm/s limit"
                    action = "Inspect bearing and alignment"
                elif motor["temperature"] > 80:
                    title = "Temperature approaching limit"
                    detail = f"Temperature is {motor['temperature']:.1f} °C; review cooling"
                    action = "Check cooling and ventilation"
                else:
                    title = "Motor health requires attention"
                    detail = f"Condition score is {health}; review motor telemetry"
                    action = "Schedule a motor inspection"
                connection.execute(
                    """INSERT INTO events
                       (id, motor_id, severity, title, detail, action, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (uuid.uuid4().hex, motor_id, state, title, detail, action, now),
                )
        if source == "ingest":
            connection.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('data_source', 'external')"
            )
        connection.commit()
        result = connection.execute("SELECT * FROM motors WHERE id=?", (motor_id,)).fetchone()
        motor_result = dict(result)
        motor_result.pop("baseline_health", None)
        motor_result.pop("baseline_sensor_risk", None)
        return motor_result


def simulation_loop() -> None:
    rng = random.Random()
    while not _simulator_stop.wait(TICK_SECONDS):
        try:
            if setting_is_paused() or data_source() != "simulation":
                continue
            motors = rows_as_dicts("SELECT * FROM motors ORDER BY rowid")
            for motor in motors:
                record_telemetry(motor["id"], {
                    "temperature": round(max(48, min(103, motor["temperature"] + rng.uniform(-0.6, 0.6))), 1),
                    "vibration": round(max(0.8, min(8.5, motor["vibration"] + rng.uniform(-0.11, 0.11))), 1),
                    "current": round(max(10, min(24, motor["current"] + rng.uniform(-0.25, 0.25))), 1),
                    "speed": max(1300, min(1520, round(motor["speed"] + rng.uniform(-6, 6)))),
                }, source="simulation")
        except (sqlite3.Error, OSError):
            LOG.exception("Unable to write the next simulated telemetry sample")


def start_simulator() -> None:
    global _simulator_thread
    _simulator_stop.clear()
    _simulator_thread = threading.Thread(target=simulation_loop, name="motorwatch-simulator", daemon=True)
    _simulator_thread.start()


def validate_readings(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    limits = {
        "temperature": (-20, 200),
        "vibration": (0, 100),
        "current": (0, 10_000),
        "speed": (0, 100_000),
    }
    result = {}
    for field, (minimum, maximum) in limits.items():
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"'{field}' must be a finite number.")
        if not minimum <= value <= maximum:
            raise ValueError(f"'{field}' must be between {minimum} and {maximum}.")
        result[field] = round(value, 1) if field != "speed" else round(value)
    return result


class MotorWatchHandler(BaseHTTPRequestHandler):
    server_version = "MotorWatch/1.0"

    def log_message(self, format_string: str, *args) -> None:
        LOG.info("%s - %s", self.address_string(), format_string % args)

    def send_json(self, status: int, body: dict) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def read_json(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json.")
        length_header = self.headers.get("Content-Length", "0")
        try:
            length = int(length_header)
        except ValueError as error:
            raise ValueError("Invalid Content-Length.") from error
        if length < 1 or length > MAX_REQUEST_BYTES:
            raise ValueError(f"Request body must be between 1 and {MAX_REQUEST_BYTES} bytes.")
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("Request body must contain valid JSON.") from error

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/health":
                self.send_json(200, {"status": "ok", "service": "MotorWatch API"})
            elif path == "/api/state":
                self.send_state()
            elif path == "/api/export.csv":
                self.send_csv()
            else:
                history_match = re.fullmatch(r"/api/motors/([A-Za-z0-9_-]+)/history", path)
                if history_match:
                    self.send_history(history_match.group(1))
                elif path.startswith("/api/"):
                    self.send_json(404, {"error": "API endpoint not found."})
                else:
                    self.serve_static(path)
        except (sqlite3.Error, OSError):
            LOG.exception("Request failed while reading application data")
            if not self.wfile.closed:
                self.send_json(500, {"error": "Unable to read application data."})

    def send_state(self) -> None:
        with DB_LOCK:
            motors = rows_as_dicts("SELECT * FROM motors ORDER BY rowid")
            for motor in motors:
                motor["risk"] = 100 - motor["health"]
                motor["updated_at"] = motor["updated_at"]
                motor.pop("baseline_health", None)
                motor.pop("baseline_sensor_risk", None)
            events = rows_as_dicts(
                """SELECT id, motor_id, severity, title, detail, action, created_at
                   FROM events WHERE acknowledged_at IS NULL ORDER BY created_at DESC"""
            )
            totals = {
                "healthy": sum(motor["state"] == "healthy" for motor in motors),
                "watch": sum(motor["state"] == "watch" for motor in motors),
                "critical": sum(motor["state"] == "critical" for motor in motors),
            }
            self.send_json(200, {
                "motors": motors,
                "events": events,
                "summary": {"total": len(motors), **totals},
                "paused": setting_is_paused(),
                "data_source": data_source(),
                "server_time": timestamp(),
                "simulation_interval_seconds": TICK_SECONDS,
            })

    def send_history(self, motor_id: str) -> None:
        with DB_LOCK:
            if get_connection().execute("SELECT 1 FROM motors WHERE id=?", (motor_id,)).fetchone() is None:
                self.send_json(404, {"error": "Motor not found."})
                return
            history = rows_as_dicts(
                """SELECT timestamp, health FROM (
                    SELECT timestamp, health FROM telemetry WHERE motor_id=?
                    ORDER BY timestamp DESC, id DESC LIMIT 30
                ) ORDER BY timestamp ASC""",
                (motor_id,),
            )
            self.send_json(200, {"motor_id": motor_id, "history": history})

    def send_csv(self) -> None:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow([
            "Asset", "Location", "Health score", "Status", "Temperature (C)",
            "Vibration (mm/s)", "Current (A)", "Speed (RPM)", "Demo risk score (%)",
        ])
        with DB_LOCK:
            motors = rows_as_dicts("SELECT * FROM motors ORDER BY rowid")
        for motor in motors:
            writer.writerow([
                motor["id"], motor["location"], motor["health"],
                {"healthy": "Healthy", "watch": "Attention", "critical": "Critical"}[motor["state"]],
                f'{motor["temperature"]:.1f}', f'{motor["vibration"]:.1f}',
                f'{motor["current"]:.1f}', motor["speed"], 100 - motor["health"],
            ])
        body = output.getvalue().encode("utf-8-sig")
        filename = f"motorwatch-fleet-{utc_now().date().isoformat()}.csv"
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/control/pause":
                payload = self.read_json()
                if not isinstance(payload, dict) or not isinstance(payload.get("paused"), bool):
                    self.send_json(400, {"error": "'paused' must be a boolean."})
                    return
                set_paused(payload["paused"])
                self.send_json(200, {"paused": payload["paused"]})
                return
            if path == "/api/control/source":
                payload = self.read_json()
                source = payload.get("source") if isinstance(payload, dict) else None
                if source not in ("simulation", "external"):
                    self.send_json(400, {"error": "'source' must be 'simulation' or 'external'."})
                    return
                set_data_source(source)
                self.send_json(200, {"data_source": source})
                return
            ack_match = re.fullmatch(r"/api/events/([A-Za-z0-9_-]+)/ack", path)
            if ack_match:
                self.acknowledge_event(ack_match.group(1))
                return
            telemetry_match = re.fullmatch(r"/api/telemetry/([A-Za-z0-9_-]+)", path)
            if telemetry_match:
                self.ingest_telemetry(telemetry_match.group(1))
                return
            self.send_json(404, {"error": "API endpoint not found."})
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
        except (sqlite3.Error, OSError):
            LOG.exception("Request failed while writing application data")
            if not self.wfile.closed:
                self.send_json(500, {"error": "Unable to save application data."})

    def acknowledge_event(self, event_id: str) -> None:
        with DB_LOCK:
            connection = get_connection()
            cursor = connection.execute(
                "UPDATE events SET acknowledged_at=? WHERE id=? AND acknowledged_at IS NULL",
                (timestamp(), event_id),
            )
            connection.commit()
            if cursor.rowcount == 0:
                exists = connection.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone()
                if exists is None:
                    self.send_json(404, {"error": "Event not found."})
                else:
                    self.send_json(409, {"error": "Event has already been acknowledged."})
                return
        self.send_json(200, {"id": event_id, "acknowledged": True})

    def ingest_telemetry(self, motor_id: str) -> None:
        readings = validate_readings(self.read_json())
        motor = record_telemetry(motor_id, readings, source="ingest")
        if motor is None:
            self.send_json(404, {"error": "Motor not found."})
            return
        self.send_json(201, {"motor": motor, "source": "ingest"})

    def serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        if relative not in {"index.html", "styles.css", "app.js"}:
            self.send_json(404, {"error": "File not found."})
            return
        target = (ROOT / relative).resolve()
        if not target.is_relative_to(ROOT) or not target.is_file():
            self.send_json(404, {"error": "File not found."})
            return
        content = target.read_bytes()
        content_type, _ = mimetypes.guess_type(target.name)
        if target.suffix == ".js":
            content_type = "text/javascript"
        if target.suffix == ".css":
            content_type = "text/css"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def create_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), MotorWatchHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local MotorWatch dashboard and API.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (defaults to localhost only).")
    parser.add_argument("--port", default=8000, type=int, help="HTTP port (default: 8000).")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    initialize_database()
    start_simulator()
    server = create_server(arguments.host, arguments.port)
    LOG.info("MotorWatch is running at http://%s:%s", arguments.host, server.server_port)
    LOG.info("SQLite database: %s", DB_PATH)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("Stopping MotorWatch.")
    finally:
        _simulator_stop.set()
        server.shutdown()
        server.server_close()
        with DB_LOCK:
            if _connection is not None:
                _connection.close()


if __name__ == "__main__":
    main()
