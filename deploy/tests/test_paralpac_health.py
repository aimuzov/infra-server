import json
import os
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "paralpac-health.mjs"


class ParalpacHealthTests(unittest.TestCase):
    def probe(self, responses):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def handle_request(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                requests.append((self.command, self.path, dict(self.headers), body))
                status, value, headers = responses[len(requests) - 1]
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for key, value_header in headers.items():
                    self.send_header(key, value_header)
                self.end_headers()
                self.wfile.write(json.dumps(value).encode())

            do_GET = handle_request
            do_POST = handle_request

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run(
                ["node", str(SCRIPT)],
                env={**os.environ, "CHATMELD_PORT": str(server.server_port),
                     "CHATMELD_USER": "admin", "CHATMELD_PASSWORD": "health-password"},
                capture_output=True, text=True, timeout=10,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        return result, requests

    def test_session_login_checks_owner_and_releases_cookie(self):
        result, requests = self.probe([
            (200, {}, {"Set-Cookie": "paralpac_session=health; Path=/; HttpOnly"}),
            (200, {"role": "owner"}, {}),
            (200, {}, {}),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([(r[0], r[1]) for r in requests], [
            ("POST", "/api/login"), ("GET", "/api/state"), ("POST", "/api/logout")])
        self.assertEqual(json.loads(requests[0][3]), {"login": "admin", "password": "health-password"})
        self.assertEqual(requests[1][2].get("cookie"), "paralpac_session=health")
        self.assertEqual(requests[2][2].get("cookie"), "paralpac_session=health")

    def test_legacy_release_keeps_basic_health_check(self):
        result, requests = self.probe([(404, {}, {}), (200, {"role": "owner"}, {})])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(requests[1][2].get("authorization"), "Basic YWRtaW46aGVhbHRoLXBhc3N3b3Jk")

    def test_failed_login_does_not_fall_back_to_basic(self):
        result, requests = self.probe([(401, {}, {})])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(requests), 1)

    def test_manager_role_fails_and_still_releases_cookie(self):
        result, requests = self.probe([
            (200, {}, {"Set-Cookie": "paralpac_session=health; Path=/"}),
            (200, {"role": "manager"}, {}),
            (200, {}, {}),
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(requests[-1][1], "/api/logout")


if __name__ == "__main__":
    unittest.main()
