from __future__ import annotations

from typing import Any, List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: .env next to the app (highest priority) or the repo-root .env, so
#: `uvicorn app.main:app` works whether the server is launched from /backend
#: or from the project root.
_ENV_FILES = ("../.env", ".env")


class Settings(BaseSettings):
    """Runtime configuration. Every value can be overridden via env vars
    with the AEROPULSE_ prefix (see /README.md and /.env.example)."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_prefix="AEROPULSE_",
        extra="ignore",
    )

    app_name: str = "AeroPulse AI Mission Control API"
    version: str = "0.1.0"

    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "info"

    # Telemetry generation
    telemetry_hz: float = Field(default=1.0, gt=0, le=60)
    orbit_period_s: float = Field(default=5760.0, gt=0)  # ~96 min LEO
    eclipse_fraction: float = Field(default=0.35, ge=0.0, lt=1.0)
    seed: Optional[int] = None

    # SIMULATED communications (orbit / ground station / RF link).
    # Everything below models a university CubeSat + one ground station; none of
    # it touches real hardware, a real radio or a real ground station.
    time_scale: float = Field(
        default=1.0, ge=1.0, le=600.0, description="Simulated seconds per wall-clock second"
    )
    ground_station_name: str = "Karachi GS (SIMULATED)"
    ground_station_lat_deg: float = Field(default=24.86, ge=-90.0, le=90.0)
    ground_station_lon_deg: float = Field(default=67.00, ge=-180.0, le=180.0)
    ground_station_min_elevation_deg: float = Field(default=10.0, ge=0.0, lt=90.0)
    orbit_inclination_deg: float = Field(default=35.0, ge=0.0, le=90.0)
    first_pass_delay_s: float = Field(
        default=15.0, ge=0.0, description="Mission-planning choice: AOS this soon after boot"
    )
    downlink_bps: float = Field(default=57600.0, gt=0, description="SIMULATED downlink bit rate")
    link_acquiring_s: float = Field(default=8.0, ge=0.0, description="AOS handshake duration")
    link_base_packet_loss: float = Field(default=0.02, ge=0.0, le=1.0)
    link_base_crc_error: float = Field(default=0.008, ge=0.0, le=1.0)
    onboard_buffer_capacity: int = Field(default=8192, gt=0, description="Onboard packet backlog cap")
    max_commands_per_pass: int = Field(default=3, gt=0)
    command_ack_delay_s: float = Field(default=3.0, ge=0.0)

    # Agent (Decide/Act). Delays pace the reasoning trace sent over /ws.
    agent_enabled: bool = True
    agent_step_delay_s: float = Field(default=0.35, ge=0.0, le=10.0)
    agent_action_delay_s: float = Field(default=0.6, ge=0.0, le=10.0)
    agent_report_delay_s: float = Field(default=0.4, ge=0.0, le=10.0)

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @field_validator("seed", mode="before")
    @classmethod
    def _blank_seed_means_random(cls, value: Any) -> Any:
        """`.env.example` ships `AEROPULSE_SEED=` (empty) to mean "random".

        Without this, copying the example file verbatim makes the app crash at
        import time with an int-parsing error.
        """
        if isinstance(value, str) and value.strip().lower() in ("", "null", "none"):
            return None
        return value

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def telemetry_interval(self) -> float:
        return 1.0 / self.telemetry_hz


settings = Settings()
