"""M7-01: the worker/beat containers answer $PORT so Cloud Run marks the revision ready."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest
from app import __version__
from app.jobs.health_server import serve_in_background


@pytest.fixture
def health_url() -> Iterator[str]:
    server = serve_in_background(port=0, component="worker")
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_healthz_matches_the_api_shape(health_url: str) -> None:
    with urllib.request.urlopen(f"{health_url}/healthz", timeout=5) as response:
        assert response.status == 200
        body = json.loads(response.read())
    assert body == {"status": "ok", "version": __version__, "component": "worker"}


def test_other_paths_are_404(health_url: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{health_url}/", timeout=5)
    assert exc.value.code == 404
