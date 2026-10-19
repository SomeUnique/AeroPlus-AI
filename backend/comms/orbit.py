"""Orbit propagation + ground-station pass prediction (SIMULATED).

A deliberately simple, fully deterministic circular-orbit model - enough to give
realistic contact windows without pulling in SGP4:

* circular LEO orbit (period from settings, ~96 min) with a fixed inclination;
* spherical Earth rotating under the orbit at the real sidereal rate;
* elevation of the spacecraft above the local horizon of a ground station.

The demo scenario is aligned so the spacecraft rises over **Karachi, Pakistan**
(24.86 N, 67.00 E) shortly after boot; after that, passes recur naturally at the
rate the geometry allows (~4-6 per simulated day for a LEO CubeSat).

This is a simulation: no real antenna, no real satellite, no real orbit data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

# --- physical constants ------------------------------------------------------ #
MU_EARTH = 3.986004418e14  # m^3/s^2
R_EARTH = 6371.0e3         # mean Earth radius [m]
OMEGA_EARTH = 7.2921159e-5  # sidereal rotation rate [rad/s]

#: Karachi ground station (SIMULATED - no real antenna).
KARACHI_LAT_DEG = 24.86
KARACHI_LON_DEG = 67.00
DEFAULT_MIN_ELEVATION_DEG = 10.0

#: Offset of the demo pass from an exactly-overhead track, so the first pass has
#: a realistic peak elevation (~70 deg) instead of a perfectly vertical one.
DEMO_PASS_OFFSET_DEG = 1.5


@dataclass(frozen=True)
class GroundStation:
    """SIMULATED ground station."""

    name: str
    lat_deg: float
    lon_deg: float
    min_elevation_deg: float = DEFAULT_MIN_ELEVATION_DEG

    @property
    def lat_rad(self) -> float:
        return math.radians(self.lat_deg)

    @property
    def lon_rad(self) -> float:
        return math.radians(self.lon_deg)

    def ecef(self) -> Tuple[float, float, float]:
        """Spherical-Earth ECEF position of the station [m]."""
        return _spherical_to_ecef(self.lat_rad, self.lon_rad, R_EARTH)


def _spherical_to_ecef(lat_rad: float, lon_rad: float, radius: float) -> Tuple[float, float, float]:
    cos_lat = math.cos(lat_rad)
    return (
        radius * cos_lat * math.cos(lon_rad),
        radius * cos_lat * math.sin(lon_rad),
        radius * math.sin(lat_rad),
    )


@dataclass(frozen=True)
class PassWindow:
    """One contact window: acquisition of signal -> loss of signal."""

    pass_id: str
    aos_s: float
    los_s: float
    max_elevation_deg: float
    """True when the scan started already inside this pass (clip, not a real AOS)."""

    def duration_s(self) -> float:
        return self.los_s - self.aos_s

    def contains(self, sim_s: float) -> bool:
        return self.aos_s <= sim_s <= self.los_s


class OrbitModel:
    """Deterministic circular LEO orbit. Positions are functions of mission seconds."""

    def __init__(
        self,
        *,
        period_s: float,
        inclination_deg: float,
        raan_rad: float,
        arg_lat0_rad: float,
    ) -> None:
        if period_s <= 0:
            raise ValueError("period must be positive")
        self.period_s = period_s
        self.inclination_rad = math.radians(inclination_deg)
        self.raan_rad = raan_rad
        self.arg_lat0_rad = arg_lat0_rad
        self.mean_motion = 2.0 * math.pi / period_s
        #: Semi-major axis implied by the period (Kepler's third law).
        self.radius_m = (MU_EARTH * period_s**2 / (4.0 * math.pi**2)) ** (1.0 / 3.0)
        self.altitude_km = (self.radius_m - R_EARTH) / 1e3

    def argument_of_latitude(self, sim_s: float) -> float:
        return self.arg_lat0_rad + self.mean_motion * sim_s

    def position_ecef(self, sim_s: float) -> Tuple[float, float, float]:
        """Spacecraft position in the rotating Earth frame [m]."""
        u = self.argument_of_latitude(sim_s)
        cos_u, sin_u = math.cos(u), math.sin(u)
        cos_i, sin_i = math.cos(self.inclination_rad), math.sin(self.inclination_rad)
        cos_o, sin_o = math.cos(self.raan_rad), math.sin(self.raan_rad)

        # Orbital plane -> ECI (ascending node on the x-axis), then ECI -> ECEF
        # by rotating the frame east by the Earth's rotation angle.
        x_orb = self.radius_m * cos_u
        y_orb = self.radius_m * sin_u * cos_i
        z_orb = self.radius_m * sin_u * sin_i

        x_eci = x_orb * cos_o - y_orb * sin_o
        y_eci = x_orb * sin_o + y_orb * cos_o
        z_eci = z_orb

        theta = OMEGA_EARTH * sim_s
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        return (
            x_eci * cos_t + y_eci * sin_t,
            -x_eci * sin_t + y_eci * cos_t,
            z_eci,
        )

    def elevation_deg(self, sim_s: float, station: GroundStation) -> float:
        """Elevation of the spacecraft above the station's local horizon [deg]."""
        sx, sy, sz = self.position_ecef(sim_s)
        gx, gy, gz = station.ecef()
        dx, dy, dz = sx - gx, sy - gy, sz - gz
        slant = math.sqrt(dx * dx + dy * dy + dz * dz)
        if slant == 0.0:
            return 90.0
        up = (dx * gx + dy * gy + dz * gz) / (slant * R_EARTH)
        return math.degrees(math.asin(max(-1.0, min(1.0, up))))

    def subpoint_latlon_deg(self, sim_s: float) -> Tuple[float, float]:
        """Sub-satellite point (used by the pass demo + tests)."""
        x, y, z = self.position_ecef(sim_s)
        r = math.sqrt(x * x + y * y + z * z)
        lat = math.degrees(math.asin(z / r))
        lon = math.degrees(math.atan2(y, x))
        return lat, lon


class PassPredictor:
    """Scans the orbit for contact windows with one ground station."""

    def __init__(
        self,
        orbit: OrbitModel,
        station: GroundStation,
        *,
        step_s: float = 5.0,
        max_horizon_s: Optional[float] = None,
    ) -> None:
        self.orbit = orbit
        self.station = station
        self.step_s = step_s
        #: How far ahead a search will look for the next pass. Gaps between passes
        #: can be several hours, so the default covers half a simulated day.
        self.max_horizon_s = max_horizon_s if max_horizon_s is not None else 43200.0

    def elevation_at(self, sim_s: float) -> float:
        return self.orbit.elevation_deg(sim_s, self.station)

    def _above(self, sim_s: float) -> bool:
        return self.elevation_at(sim_s) >= self.station.min_elevation_deg

    def _bisect_crossing(self, low: float, high: float) -> float:
        """Refine a horizon crossing between ``low`` (above) and ``high`` (below) or vice versa."""
        for _ in range(24):
            mid = 0.5 * (low + high)
            if self._above(mid) == self._above(low):
                low = mid
            else:
                high = mid
        return 0.5 * (low + high)

    def _peak_elevation(self, coarse_s: float, half_window: float) -> Tuple[float, float]:
        """Refine the peak elevation around a coarse sample with a fine scan."""
        best_s, best_el = coarse_s, self.elevation_at(coarse_s)
        t = coarse_s - half_window
        while t <= coarse_s + half_window:
            el = self.elevation_at(t)
            if el > best_el:
                best_s, best_el = t, el
            t += 0.5
        return best_el, best_s

    def passes_between(self, start_s: float, end_s: float) -> List[PassWindow]:
        """Every pass whose AOS falls inside ``[start_s, end_s]``."""
        windows: List[PassWindow] = []
        t = start_s
        while True:
            window = self.find_pass_after(t)
            if window is None or window.aos_s > end_s:
                break
            windows.append(window)
            # Step clear of the mask edge so the just-ended pass is not re-detected.
            t = window.los_s + self.step_s
        return windows

    def find_pass_after(self, sim_s: float) -> Optional[PassWindow]:
        """Next contact window starting at or after ``sim_s`` (None within horizon).

        If the spacecraft is already above the mask at ``sim_s`` the current pass
        is returned clipped to ``aos_s = sim_s``.
        """
        t = sim_s
        horizon = sim_s + self.max_horizon_s
        in_pass = self._above(t)
        aos = t if in_pass else None
        coarse_peak_s = t
        peak_el = self.elevation_at(t)

        while t < horizon:
            nxt = t + self.step_s
            above = self._above(nxt)
            if above:
                if self.elevation_at(nxt) > peak_el:
                    peak_el, coarse_peak_s = self.elevation_at(nxt), nxt
                if aos is None:
                    aos = self._bisect_crossing(t, nxt)
                    coarse_peak_s, peak_el = aos, self.elevation_at(aos)
            elif aos is not None:
                los = self._bisect_crossing(nxt, t)
                best_el, _ = self._peak_elevation(coarse_peak_s, self.step_s + 1.0)
                return PassWindow(
                    pass_id=f"pass_{int(round(aos))}",
                    aos_s=aos,
                    los_s=los,
                    max_elevation_deg=best_el,
                )
            t = nxt
        return None


def demo_geometry(
    *,
    target_tca_s: float,
    station: GroundStation,
    period_s: float,
    inclination_deg: float,
    offset_deg: float = DEMO_PASS_OFFSET_DEG,
) -> Tuple[float, float]:
    """Choose ``(raan, arg_lat0)`` so the spacecraft peaks near the station at TCA.

    Closed form: place the sub-satellite point a small longitude offset away from
    the station at the target time of closest approach, then solve the orbital
    element that puts the spacecraft there. Used to guarantee an early first pass
    in the demo scenario (a mission-planning choice, not fake data).
    """
    inclination_rad = math.radians(inclination_deg)
    sin_i, cos_i = math.sin(inclination_rad), math.cos(inclination_rad)

    target_lat = math.radians(station.lat_deg)
    target_lon = math.radians(station.lon_deg + offset_deg)

    # Desired sub-point in the rotating frame at TCA -> ECI direction of the craft.
    ex, ey, ez = _spherical_to_ecef(target_lat, target_lon, 1.0)
    theta = OMEGA_EARTH * target_tca_s
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    px = ex * cos_t - ey * sin_t
    py = ex * sin_t + ey * cos_t
    pz = ez

    if abs(pz) > abs(sin_i):
        raise ValueError("inclination too low to reach the station latitude")

    u = math.asin(max(-1.0, min(1.0, pz / sin_i)))  # ascending branch (cos u > 0)
    cos_u, sin_u = math.cos(u), math.sin(u)
    raan = math.atan2(py, px) - math.atan2(sin_u * cos_i, cos_u)
    mean_motion = 2.0 * math.pi / period_s
    arg_lat0 = u - mean_motion * target_tca_s
    return _wrap(raan), _wrap(arg_lat0)


def align_first_pass(
    *,
    station: GroundStation,
    period_s: float,
    inclination_deg: float,
    first_pass_delay_s: float,
    estimate_half_pass_s: float = 240.0,
    iterations: int = 6,
    predictor_factory=None,
) -> Tuple[OrbitModel, PassWindow]:
    """Solve the initial orbit so the first AOS happens ~``first_pass_delay_s`` in.

    The pass duration is geometry-fixed, so shifting the target TCA by the AOS
    error converges in a single step; a few iterations are run for safety.
    """
    target = first_pass_delay_s + estimate_half_pass_s
    orbit: Optional[OrbitModel] = None
    window: Optional[PassWindow] = None
    for _ in range(max(1, iterations)):
        raan, arg_lat0 = demo_geometry(
            target_tca_s=target,
            station=station,
            period_s=period_s,
            inclination_deg=inclination_deg,
        )
        orbit = OrbitModel(
            period_s=period_s,
            inclination_deg=inclination_deg,
            raan_rad=raan,
            arg_lat0_rad=arg_lat0,
        )
        predictor = (
            predictor_factory(orbit) if predictor_factory else PassPredictor(orbit, station)
        )
        window = predictor.find_pass_after(0.0)
        if window is None:
            target += period_s / 2.0
            continue
        drift = window.aos_s - first_pass_delay_s
        if abs(drift) < 2.0:
            break
        target -= drift
    assert orbit is not None and window is not None, "failed to align the demo first pass"
    return orbit, window


def upcoming_pass_summary(
    predictor: PassPredictor, now_s: float
) -> Optional[Tuple[PassWindow, float]]:
    """Next pass after ``now_s`` plus the countdown in simulated seconds."""
    window = predictor.find_pass_after(now_s)
    if window is None:
        return None
    return window, max(0.0, window.aos_s - now_s)


def _wrap(angle: float) -> float:
    two_pi = 2.0 * math.pi
    return angle % two_pi
