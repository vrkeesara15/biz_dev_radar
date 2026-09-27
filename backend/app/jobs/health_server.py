"""A one-route HTTP server so Celery can run as a Cloud Run *service*.

Cloud Run services must answer on `$PORT` or the revision never becomes ready, but a Celery
worker or beat process speaks only to Redis. The container entrypoint therefore starts this
thread next to Celery; it answers `GET /healthz` with the same body as the API's route and
404 for everything else. It is deliberately stdlib-only (no uvicorn, no FastAPI app, no DB)
so a wedged worker still reports its process as alive and Cloud Run's health check tells us
about the container rather than about Postgres.

    python -m app.jobs.health_server           # foreground, for debugging
    serve_in_background(port=8080)             # what the entrypoint uses
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from app import __version__

DEFAULT_PORT = 8080


class _Handler(BaseHTTPRequestHandler):
    server_version = "bidradar-health"
    component = "worker"

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/healthz":
            self.send_error(404)
            return
        body = json.dumps(
            {"status": "ok", "version": __version__, "component": self.component}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Silence the default stderr access log; Cloud Run probes every few seconds."""


def make_server(port: int = DEFAULT_PORT, component: str = "worker") -> ThreadingHTTPServer:
    handler = type("_ComponentHandler", (_Handler,), {"component": component})
    return ThreadingHTTPServer(("0.0.0.0", port), handler)


def serve_in_background(port: int = DEFAULT_PORT, component: str = "worker") -> ThreadingHTTPServer:
    server = make_server(port, component)
    threading.Thread(target=server.serve_forever, name="healthz", daemon=True).start()
    return server


def main() -> int:  # pragma: no cover - exercised by the container, not by tests
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    component = os.environ.get("BIDRADAR_COMPONENT", "worker")
    make_server(port, component).serve_forever()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
