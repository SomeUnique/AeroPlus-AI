"""Simulated mission clock (SIMULATED).

Everything in AeroPulse runs on a *simulated* mission timeline, not wall time:

* :attr:`epoch` is the UTC instant the simulation started (it maps mission
  seconds to timestamps on the wire);
* :attr:`time_scale` is the demo time-acceleration factor - how many simulated
  seconds advance per wall-clock second. At ``time_scale=120`` a ~96 min orbit
  takes ~48 s of wall time, so a Karachi pass happens every couple of minutes.

Mission seconds themselves are owned by :class:`services.telemetry.TelemetryEngine`
(``engine.elapsed``) so the physics, the orbit model and the RF link can never
drift apart: they all read the same counter.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Tuple

#: Demo acceleration limits. 1x is real time; 600x is the fastest demo speed.
MIN_TIME_SCALE = 1.0
MAX_TIME_SCALE = 600.0


class MissionClock:
    """Holds the simulated epoch and the demo time-acceleration factor."""

    def __init__(self, *, epoch: datetime | None = None, time_scale: float = 1.0) -> None:
        self.epoch = epoch or datetime.now(timezone.utc)
        self.time_scale = self._clamp(time_scale)

    @staticmethod
    def _clamp(scale: float) -> float:
        return max(MIN_TIME_SCALE, min(MAX_TIME_SCALE, float(scale)))

    def set_time_scale(self, scale: float) -> Tuple[float, float]:
        """Set the acceleration factor. Returns ``(previous, current)``."""
        previous = self.time_scale
        self.time_scale = self._clamp(scale)
        return previous, self.time_scale

    def datetime_at(self, sim_s: float) -> datetime:
        """Mission seconds -> simulated UTC timestamp."""
        return self.epoch + timedelta(seconds=sim_s)

    def seconds_between(self, sooner: datetime, later: datetime) -> float:
        return (later - sooner).total_seconds()
