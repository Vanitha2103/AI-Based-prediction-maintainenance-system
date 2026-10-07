import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

import backend


class MotorWatchApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.original_db_path = backend.DB_PATH
        backend.DB_PATH = Path(cls.temporary_directory.name) / "test.db"
        backend._connection = None
        backend.initialize_database()
        cls.server = backend.create_server(port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        if backend._connection is not None:
            backend._connection.close()
        backend._connection = None
        backend.DB_PATH = cls.original_db_path
        cls.temporary_directory.cleanup()

    def request(self, method, path, body=None, content_type="application/json"):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=3)
        encoded = None if body is None else json.dumps(body)
        headers = {}
        if encoded is not None:
            headers["Content-Type"] = content_type
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        result = response.status, response.getheader("Content-Type"), payload
        connection.close()
        return result

    def test_dashboard_is_served_and_state_is_persisted(self):
        status, content_type, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn(b"MotorWatch", body)

        status, _, body = self.request("GET", "/api/state")
        state = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(state["summary"]["total"], 8)
        self.assertEqual(len(state["events"]), 3)
        self.assertEqual(len(state["motors"]), 8)

        status, _, body = self.request("GET", "/api/motors/M-204/history")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["history"]), 30)

    def test_pause_and_event_acknowledgement_persist(self):
        status, _, body = self.request("POST", "/api/control/pause", {"paused": True})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["paused"])

        status, _, body = self.request("POST", "/api/events/evt-1/ack", {})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["acknowledged"])

        status, _, body = self.request("GET", "/api/state")
        state = json.loads(body)
        self.assertTrue(state["paused"])
        self.assertEqual(len(state["events"]), 2)
        backend.set_paused(False)

    def test_telemetry_ingestion_updates_motor_and_rejects_invalid_values(self):
        readings = {"temperature": 83.2, "vibration": 5.4, "current": 19.1, "speed": 1420}
        status, _, body = self.request("POST", "/api/telemetry/M-204", readings)
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body)["motor"]["vibration"], 5.4)
        status, _, body = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["data_source"], "external")

        status, _, body = self.request(
            "POST", "/api/control/source", {"source": "simulation"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["data_source"], "simulation")

        status, _, body = self.request(
            "POST", "/api/telemetry/M-204", {**readings, "temperature": float("nan")}
        )
        self.assertEqual(status, 400)
        self.assertIn("finite number", json.loads(body)["error"])

        status, _, body = self.request("POST", "/api/telemetry/M-999", readings)
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"], "Motor not found.")

    def test_export_and_unknown_routes(self):
        status, content_type, body = self.request("GET", "/api/export.csv")
        self.assertEqual(status, 200)
        self.assertIn("text/csv", content_type)
        self.assertEqual(len(body.decode("utf-8-sig").splitlines()), 9)

        status, _, body = self.request("GET", "/api/motors/../../README.md")
        self.assertIn(status, (400, 404))

        status, _, _ = self.request("GET", "/backend.py")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
