"""Detection configuration: thresholds, subsystem map, incident rules.

Thresholds below were tuned against a 3600 s nominal run of the telemetry
engine (see backend/tests/test_detection.py). Measured nominal extremes:

    sensor             min        max     | max 10 s regression slope
    battery_voltage   27.07      28.23     | 0.016 /s
    battery_temp      21.02      42.77     | 0.111 /s
    solar_current      0.00       6.79     | 0.090 /s  (terminator crossing)
    bus_temp          15.02      32.36     | 0.067 /s
    signal_strength  -89.43     -61.22     | 0.673 /s  (orbital + walk)
    gyro_rate          0.00       0.155    | 0.017 /s
    payload_power      1.32      47.03     | 5.559 /s  (mode switching)

Rule thresholds sit outside the nominal envelope with margin; statistical
thresholds are gated + persistence-filtered so orbital dynamics and mode
switches never trip them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional

from services.telemetry import SOLAR_NOMINAL_A

# --- Detector types ---
class DetectorType(str, Enum):
    RULE = "rule"
    ZSCORE = "zscore"
    RATE = "rate"
    CORRELATED = "correlated"
    IFOREST = "iforest"


# --- Subsystems ---
class Subsystem(str, Enum):
    POWER = "Power"
    THERMAL = "Thermal"
    COMMS = "Comms"
    PAYLOAD = "Payload"
    ADCS = "ADCS"
    UNKNOWN = "Unknown"


class SystemStatus(str, Enum):
    NOMINAL = "NOMINAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


#: Subsystems that carry an explicit status (all live subsystems).
STATUS_SUBSYSTEMS = (
    Subsystem.POWER,
    Subsystem.THERMAL,
    Subsystem.COMMS,
    Subsystem.PAYLOAD,
    Subsystem.ADCS,
)


class Direction(str, Enum):
    HIGH_BAD = "high_bad"   # value above the limit is bad
    LOW_BAD = "low_bad"     # value below the limit is bad


# --- Statistical detector settings ---
Z_THRESH = 3.0                 # 3-sigma, as specified
ZSCORE_WINDOW_SEC = 90.0
ROC_WINDOW_SEC = 20.0          # least-squares slope window
ZSCORE_MIN_SAMPLES = 30
ROC_MIN_SAMPLES = 12

# --- Incident grouping / debounce ---
INCIDENT_DEBOUNCE_SEC = 10.0   # min gap between anomaly broadcasts per subsystem
INCIDENT_EXPIRY_SEC = 90.0     # incident auto-resolves after this quiet period
MAX_INCIDENT_ANOMALIES = 50    # ring-buffer cap so long-lived incidents don't grow


@dataclass(frozen=True)
class SensorSpec:
    """Per-sensor detection configuration."""

    name: str
    subsystem: Subsystem
    # rule thresholds
    warn: Optional[float] = None
    crit: Optional[float] = None
    direction: Direction = Direction.HIGH_BAD
    #: rule only evaluated when sun_fraction >= this (e.g. solar current)
    sunlit_min: Optional[float] = None
    # statistical
    z_window_sec: float = ZSCORE_WINDOW_SEC
    roc_window_sec: float = ROC_WINDOW_SEC
    roc_thresh: Optional[float] = None   # per-second slope; None disables ROC
    #: z-score on (value - model) instead of the raw value (solar illumination)
    model_residual: bool = False
    #: disable the rolling z-score where nominal steps/swings dominate the signal
    #: (eclipse battery step, ADCS slew spikes, payload mode switches)
    zscore_enabled: bool = True
    #: consecutive breaches required before a statistical anomaly is emitted
    min_consecutive: int = 3

    def expected_model(self, reading) -> Optional[float]:
        if self.model_residual and self.name == "solar_current":
            return SOLAR_NOMINAL_A * float(getattr(reading, "sun_fraction", 1.0))
        return None


SENSORS: Dict[str, SensorSpec] = {
    "battery_voltage": SensorSpec(
        name="battery_voltage", subsystem=Subsystem.POWER,
        warn=25.5, crit=24.0, direction=Direction.LOW_BAD,
        roc_thresh=0.06, min_consecutive=3,
        # eclipse entry applies a sustained load-sag step -> raw z false-alarms
        zscore_enabled=False,
    ),
    "battery_temp": SensorSpec(
        name="battery_temp", subsystem=Subsystem.THERMAL,
        warn=48.0, crit=55.0, direction=Direction.HIGH_BAD,
        roc_thresh=0.20, roc_window_sec=20.0, min_consecutive=3,
        # thermal target steps with payload/sun -> raw z is non-stationary
        zscore_enabled=False,
    ),
    "solar_current": SensorSpec(
        name="solar_current", subsystem=Subsystem.POWER,
        warn=4.3, crit=3.0, direction=Direction.LOW_BAD, sunlit_min=0.80,
        roc_thresh=None, model_residual=True, z_window_sec=60.0, min_consecutive=3,
    ),
    "bus_temp": SensorSpec(
        name="bus_temp", subsystem=Subsystem.THERMAL,
        warn=37.0, crit=42.0, direction=Direction.HIGH_BAD,
        roc_thresh=0.20, min_consecutive=3,
        # non-stationary like battery_temp (sun/payload driven)
        zscore_enabled=False,
    ),
    "signal_strength": SensorSpec(
        name="signal_strength", subsystem=Subsystem.COMMS,
        warn=-98.0, crit=-108.0, direction=Direction.LOW_BAD,
        roc_thresh=None, z_window_sec=120.0, min_consecutive=5,
    ),
    "gyro_rate": SensorSpec(
        name="gyro_rate", subsystem=Subsystem.ADCS,
        warn=0.30, crit=0.50, direction=Direction.HIGH_BAD,
        roc_thresh=0.10, min_consecutive=3,
        # 3-8 s attitude slews are nominal; raw z would flag them
        zscore_enabled=False,
    ),
    "payload_power": SensorSpec(
        name="payload_power", subsystem=Subsystem.PAYLOAD,
        warn=55.0, crit=60.0, direction=Direction.HIGH_BAD,
        roc_thresh=None, min_consecutive=3,   # mode switching = legit fast ramps
        zscore_enabled=False,
    ),
}
