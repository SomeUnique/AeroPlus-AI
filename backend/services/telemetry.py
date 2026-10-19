"""Synthetic telemetry generator.

Physics-ish model running at 1 Hz (configurable):

* slow orbital cycle (~96 min) with a smooth sunlight -> eclipse transition
  that drives solar current, temperatures and (via charge/discharge) battery
  voltage;
* per-sensor Gaussian noise plus slow random walks so traces are never flat;
* payload duty cycle (idle / imaging / downlink) that perturbs load, power
  and body rate;
* fault effects folded in from :mod:`services.faults`.

The engine is deterministic given a seed and wall-clock free: time advances by
the ``dt`` passed to :meth:`tick`, so tests can fast-forward an orbit.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from models.schemas import PayloadMode, TelemetryReading
from services.faults import FaultManager

# Spacecraft constants ------------------------------------------------------- #
ORBIT_PERIOD_S = 5760.0          # ~96 min LEO
ECLIPSE_FRACTION = 0.35
BUS_NOMINAL_V = 28.5
BATTERY_CAPACITY_AH = 10.0
SOLAR_NOMINAL_A = 6.6            # array output in full sun
BASE_LOAD_A = 2.9                # avionics/thermal load, excludes payload
SUN_EDGE_SHARPNESS = 0.02        # logistic width of the terminator crossing (~80 s)

# Payload duty cycle: period, imaging window, downlink window --------------- #
PAYLOAD_PERIOD_S = 300.0
IMAGING_END_S = 70.0
DOWNLINK_END_S = 115.0
PAYLOAD_W = {PayloadMode.IDLE: 2.5, PayloadMode.IMAGING: 46.0, PayloadMode.DOWNLINK: 28.0}


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class EngineStats:
    generated: int = 0
    dropped: int = 0
    orbits_completed: int = 0
    started_at: float = field(default_factory=lambda: datetime.now(timezone.utc))


class TelemetryEngine:
    def __init__(
        self,
        faults: FaultManager,
        telemetry_hz: float = 1.0,
        orbit_period_s: float = ORBIT_PERIOD_S,
        eclipse_fraction: float = ECLIPSE_FRACTION,
        seed: Optional[int] = None,
        *,
        epoch: Optional[datetime] = None,
        dt_clip_s: float = 30.0,
    ) -> None:
        self.faults = faults
        self.telemetry_hz = telemetry_hz
        self.orbit_period_s = orbit_period_s
        self.eclipse_fraction = eclipse_fraction
        self.rng = random.Random(seed)
        #: Mission-time origin. Reading timestamps are ``epoch + elapsed`` so the
        #: whole simulation shares one clock (see services/clock.py).
        self.epoch = epoch or datetime.now(timezone.utc)
        #: Largest single integration step. Raised by POST /time-scale so a
        #: deliberately accelerated demo is never silently clamped.
        self.dt_clip_s = dt_clip_s

        # Logistic offset so that the sunlit fraction == 1 - eclipse_fraction.
        self._sun_offset = -math.cos(math.pi * (1.0 - eclipse_fraction))
        self._phase_offset = self.rng.random()  # start anywhere in the orbit

        self.stats = EngineStats()
        self._t = 0.0
        self._last_reading: Optional[TelemetryReading] = None
        self._reset_physics()

    # -- public API ----------------------------------------------------------- #
    @property
    def elapsed(self) -> float:
        return self._t

    @property
    def last_reading(self) -> Optional[TelemetryReading]:
        return self._last_reading

    def reset_physics(self) -> None:
        """Return the spacecraft to its nominal state (does not touch faults)."""
        self._reset_physics()

    def sunlight(self, phase: Optional[float] = None) -> float:
        """0 (deep eclipse) .. 1 (full sun), smooth at the terminator."""
        if phase is None:
            phase = self.orbit_phase
        arg = 2.0 * math.pi * (phase - self._phase_offset)
        return 1.0 / (1.0 + math.exp(-(math.cos(arg) + self._sun_offset) / SUN_EDGE_SHARPNESS))

    @property
    def orbit_phase(self) -> float:
        return (self._t / self.orbit_period_s) % 1.0

    def tick(self, dt: float) -> Optional[TelemetryReading]:
        """Advance the simulation by ``dt`` seconds and produce one reading.

        Returns ``None`` when the frame is lost to packet loss (only while the
        signal_loss fault is active). State still advances in that case.
        """
        dt = _clip(dt, 0.0, self.dt_clip_s)
        self._t += dt
        if dt > 0:
            self.faults.tick(dt)
        effects = self.faults.effects()

        reading = self._step(dt, effects)
        self.stats.generated += 1
        self._last_reading = reading

        if effects.packet_loss_prob > 0.0 and self.rng.random() < effects.packet_loss_prob:
            self.stats.dropped += 1
            return None
        return reading

    # -- internals ------------------------------------------------------------ #
    def _reset_physics(self) -> None:
        self._soc = 0.88
        self._bus_temp = 20.0
        self._battery_temp = 21.0
        self._gyro_bias = self.rng.gauss(0.0, 0.002)
        self._signal_walk = 0.0
        self._payload_power = PAYLOAD_W[PayloadMode.IDLE]
        self._maneuver_until = -1.0
        self._last_payload_mode = self._payload_mode()

    def _payload_mode(self) -> PayloadMode:
        cycle = self._t % PAYLOAD_PERIOD_S
        if cycle < IMAGING_END_S:
            return PayloadMode.IMAGING
        if cycle < DOWNLINK_END_S:
            return PayloadMode.DOWNLINK
        return PayloadMode.IDLE

    def _step(self, dt: float, effects) -> TelemetryReading:
        rng = self.rng
        sun = self.sunlight()
        in_eclipse = sun < 0.5

        # Payload duty cycle with a short power-converter lag -------------- #
        mode = self._payload_mode()
        target_w = PAYLOAD_W[mode]
        self._payload_power += (target_w - self._payload_power) * min(1.0, dt / 3.0)
        payload_power = max(0.0, self._payload_power + rng.gauss(0.0, 0.35))

        # Solar array ------------------------------------------------------ #
        solar_current = SOLAR_NOMINAL_A * sun * effects.solar_multiplier
        solar_current += rng.gauss(0.0, 0.06) + (0.02 if in_eclipse else 0.0)
        solar_current = max(0.0, solar_current)

        # Power balance -> battery state of charge ------------------------- #
        load_current = BASE_LOAD_A + payload_power / BUS_NOMINAL_V + rng.gauss(0.0, 0.05)
        net_current = solar_current - load_current

        charge_allowed = self._soc < 0.985
        usable_net = net_current if (net_current > 0.0 and charge_allowed) else net_current
        if net_current > 0.0 and not charge_allowed:
            usable_net = 0.0
        self._soc = _clip(self._soc + usable_net * dt / 3600.0 / BATTERY_CAPACITY_AH, 0.10, 1.0)

        battery_voltage = (
            24.0
            + 4.5 * self._soc
            - 0.06 * max(0.0, -net_current)          # load transient sag
            + rng.gauss(0.0, 0.035)
            - effects.battery_voltage_sag_v
        )
        battery_voltage = _clip(battery_voltage, 18.0, 33.0)

        # Thermal ---------------------------------------------------------- #
        bus_target = (
            15.0
            + 15.0 * sun
            + 0.35 * load_current
            + 1.4 * math.sin(2.0 * math.pi * self._t / 1400.0)
        )
        self._bus_temp += (bus_target - self._bus_temp) * min(1.0, dt / 200.0)
        bus_temp = self._bus_temp + rng.gauss(0.0, 0.05)

        battery_target = (
            self._bus_temp
            + 2.6 * abs(net_current)
            + 1.5 * sun
            + effects.battery_temp_add_c
        )
        self._battery_temp += (battery_target - self._battery_temp) * min(1.0, dt / 90.0)
        battery_temp = self._battery_temp + rng.gauss(0.0, 0.06)

        # RF link ---------------------------------------------------------- #
        self._signal_walk = self._signal_walk * 0.995 + rng.gauss(0.0, 0.12)
        signal_strength = (
            -74.0
            + 10.0 * math.sin(2.0 * math.pi * self._t / 1100.0 + 1.1)
            + self._signal_walk
            + rng.gauss(0.0, 0.7)
            + effects.signal_offset_dbm
        )
        if rng.random() < 0.01:  # scintillation fade
            signal_strength -= rng.uniform(3.0, 7.0)
        signal_strength = _clip(signal_strength, -125.0, -50.0)

        # Attitude (body rate) --------------------------------------------- #
        if self._t > self._maneuver_until and rng.random() < 0.004:
            self._maneuver_until = self._t + rng.uniform(3.0, 8.0)
        if self._t <= self._maneuver_until:
            gyro_rate = abs(rng.gauss(0.09, 0.03))
        else:
            self._gyro_bias = _clip(self._gyro_bias + rng.gauss(0.0, 4e-5), -0.01, 0.01)
            gyro_rate = abs(self._gyro_bias + rng.gauss(0.0, 0.0025))
        gyro_rate = round(gyro_rate, 5)

        return TelemetryReading(
            timestamp=self.epoch + timedelta(seconds=self._t),
            battery_voltage=round(battery_voltage, 3),
            battery_temp=round(battery_temp, 2),
            solar_current=round(solar_current, 4),
            bus_temp=round(bus_temp, 2),
            signal_strength=round(signal_strength, 2),
            gyro_rate=gyro_rate,
            payload_power=round(payload_power, 2),
            in_eclipse=in_eclipse,
            sun_fraction=round(sun, 4),
            payload_mode=mode,
        )


def nominal_ranges() -> List[dict]:
    """Expected operating envelopes - reused by detection and tests."""
    return [
        {"sensor": "battery_voltage", "min": 24.0, "max": 32.0, "unit": "V"},
        {"sensor": "battery_temp", "min": 10.0, "max": 45.0, "unit": "degC"},
        {"sensor": "solar_current", "min": 0.0, "max": 7.5, "unit": "A"},
        {"sensor": "bus_temp", "min": 5.0, "max": 40.0, "unit": "degC"},
        {"sensor": "signal_strength", "min": -100.0, "max": -60.0, "unit": "dBm"},
        {"sensor": "gyro_rate", "min": 0.0, "max": 0.25, "unit": "deg/s"},
        {"sensor": "payload_power", "min": 0.0, "max": 55.0, "unit": "W"},
    ]
