from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    """FastAPI client with lifespan (starts/stops the telemetry loop).

    The first ground-station pass is pulled to t=0 (and the AOS handshake
    removed) so tests reach the downlink quickly instead of waiting ~15 s of
    wall time for the mission-planned AOS.
    """
    import app.main as main_module
    from app.main import app

    monkeypatch.setattr(main_module.settings, "first_pass_delay_s", 0.0)
    monkeypatch.setattr(main_module.settings, "link_acquiring_s", 0.0)
    with TestClient(app) as test_client:
        yield test_client
