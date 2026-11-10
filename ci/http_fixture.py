"""Serve controlled HTML, JSON, and robots content for the Compose smoke job."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HTML = b"""<!doctype html>
<html><head><title>LeashByte fixture</title></head>
<body><article><h1>LeashByte controlled page</h1>
<p>This controlled page provides enough readable text for the extraction check.</p>
<p>The fetch service reads this page inside an isolated test environment.</p>
</article></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/robots.txt":
            body, content_type = b"User-agent: *\nAllow: /\n", "text/plain"
        elif self.path == "/html":
            body, content_type = HTML, "text/html; charset=utf-8"
        elif self.path == "/json":
            body = json.dumps({"source": "controlled-fixture", "value": 42}).encode()
            content_type = "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    host = os.environ.get("FIXTURE_HOST", "0.0.0.0")
    port = int(os.environ.get("FIXTURE_PORT", "18765"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()
