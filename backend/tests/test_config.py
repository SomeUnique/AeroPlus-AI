"""Config/.env behaviour: the documented `.env.example -> .env` workflow must
work whether the server is launched from /backend or from the repo root."""

from __future__ import annotations

import importlib
import os
import pathlib

import pytest

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def restore_app_config():
    """Reload app.config from the real backend dir after each test so the
    module-level `settings` singleton is never left pointing at a tmp project."""
    yield
    os.chdir(BACKEND_DIR)
    import app.config as cfg

    importlib.reload(cfg)


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A throwaway project with a repo root and a backend/ dir."""
    proj = tmp_path / "proj"
    backend = proj / "backend"
    backend.mkdir(parents=True)
    monkeypatch.chdir(backend)
    for key in ("AEROPULSE_SEED", "AEROPULSE_TELEMETRY_HZ"):
        monkeypatch.delenv(key, raising=False)
    return proj, backend


def load_settings():
    import app.config as cfg

    importlib.reload(cfg)
    return cfg.Settings()


def test_blank_seed_means_random_and_root_env_is_found(project):
    proj, _ = project
    (proj / ".env").write_text("AEROPULSE_SEED=\nAEROPULSE_TELEMETRY_HZ=2\n", encoding="utf-8")
    settings = load_settings()
    assert settings.seed is None
    assert settings.telemetry_hz == 2.0


def test_backend_env_wins_over_root_env(project):
    proj, backend = project
    (proj / ".env").write_text("AEROPULSE_SEED=\n", encoding="utf-8")
    (backend / ".env").write_text("AEROPULSE_SEED=42\n", encoding="utf-8")
    assert load_settings().seed == 42


def test_environment_variable_wins_over_files(project, monkeypatch):
    proj, backend = project
    (proj / ".env").write_text("AEROPULSE_SEED=42\n", encoding="utf-8")
    monkeypatch.setenv("AEROPULSE_SEED", "7")
    assert load_settings().seed == 7


def test_defaults_without_any_env_file(project):
    settings = load_settings()
    assert settings.seed is None
    assert settings.telemetry_hz == 1.0
    assert "http://localhost:3000" in settings.cors_origin_list
