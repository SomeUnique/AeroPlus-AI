"""Detector: rule thresholds + rolling z-score + rate-of-change + Isolation Forest.

Design notes
------------
* Rule thresholds are absolute (context-aware: solar current only when sunlit)
  and sit outside the nominal envelope with margin.
* Statistical detectors are *persistence filtered*: a breach must repeat for
  `min_consecutive` readings before an anomaly is emitted, which keeps orbital
  dynamics, terminator crossings, random-walk noise, ADCS slews and payload
  mode switches from raising false alarms while still catching genuine drifts
  within a few seconds.
* The solar z-score runs on the *model residual* (measured - 6.6 A * sun_fraction),
  so it reacts to array degradation regardless of eclipse.
* The ML layer (Isolation Forest over windowed mean/std/slope features) runs on
  the same post-link stream and is trained offline on nominal telemetry by
  ``backend/tools/train_iforest.py``; without the artifact it stays silent.

Post-link robustness - detection runs on data *after* it arrives at the
ground, so it must survive an imperfect downlink:

* **Missing packets**: every statistical window is trimmed by *time*, not by
  sample count alone, and each detector has a minimum-sample gate, so a gap
  simply pauses the statistical layers until the window refills.
* **Delayed / out-of-order packets**: a reading more than ``LATE_TOLERANCE_S``
  behind the newest processed mission time is *late*. Late readings still get
  the value-based rule check (valid at any time) but are excluded from the
  time-ordered statistics and the ML window, so a burst of replayed packets
  cannot poison baselines or trigger false drift alarms.
* **Large gaps** (``GAP_RESET_S``, e.g. after a long outage) reset the
  persistence counters so pre-gap and post-gap hits are never summed.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Union

from detection.buffers import RollingBuffer
from detection.config import (
    SENSORS,
    ZSCORE_MIN_SAMPLES,
    Z_THRESH,
    DetectorType,
    Direction,
    SensorSpec,
)
from detection.ml.iforest import IsolationForestModel, load_model
from detection.ml.stream import MLStreamDetector
from detection.models import Anomaly

#: a reading this far behind the newest processed time is treated as late
LATE_TOLERANCE_S = 2.0
#: a jump forward this large resets statistical persistence counters
GAP_RESET_S = 300.0


class Detector:
    """Stateful per-sensor detector. Feed it one reading at a time."""

    def __init__(
        self,
        specs: Optional[Dict[str, SensorSpec]] = None,
        *,
        ml: Union[str, IsolationForestModel, None] = "auto",
    ) -> None:
        self.specs = specs or SENSORS
        #: "auto" loads detection/ml/artifacts/iforest.joblib when present
        self._ml_option = ml
        self.reset()

    def reset(self) -> None:
        """Drop all history (called on /reset so faults don't poison baselines)."""
        self.z_buffers: Dict[str, RollingBuffer] = {
            name: RollingBuffer(max_sec=spec.z_window_sec) for name, spec in self.specs.items()
        }
        self.roc_buffers: Dict[str, RollingBuffer] = {
            name: RollingBuffer(max_sec=spec.roc_window_sec) for name, spec in self.specs.items()
        }
        self._z_hits: Dict[str, int] = {name: 0 for name in self.specs}
        self._roc_hits: Dict[str, int] = {name: 0 for name in self.specs}
        self._last_t: float = float("-inf")
        self.late_readings = 0
        self.gaps_seen = 0
        self.ml = self._make_ml_detector()

    def _make_ml_detector(self) -> Optional[MLStreamDetector]:
        option = self._ml_option
        if option is None:
            return None
        model = option if isinstance(option, IsolationForestModel) else load_model()
        if model is None:
            return None
        return MLStreamDetector(model)

    def feed(self, reading, now: float) -> List[Anomaly]:
        now = float(now)
        late = now < self._last_t - LATE_TOLERANCE_S
        gap = now - self._last_t > GAP_RESET_S
        if gap:
            self.gaps_seen += 1
            self._z_hits = {name: 0 for name in self.specs}
            self._roc_hits = {name: 0 for name in self.specs}
            if self.ml is not None:
                self.ml._hits = 0
        if late:
            self.late_readings += 1
        else:
            self._last_t = now

        anomalies: List[Anomaly] = []
        values: Dict[str, float] = {}
        for name, spec in self.specs.items():
            value = getattr(reading, name, None)
            if value is None:
                continue
            value = float(value)
            values[name] = value

            # The rule layer is value-based and valid at any arrival time.
            rule = self._check_rule(spec, value, reading)
            if rule is not None:
                if late:
                    rule.extra["late_arrival"] = True
                anomalies.append(rule)

            if late:
                # Late/replayed packet: never insert into time-ordered windows.
                continue

            # Statistics are always maintained so baselines stay warm.
            self.roc_buffers[name].add(now, value)
            expected = spec.expected_model(reading)
            self.z_buffers[name].add(now, value - expected if expected is not None else value)

            if spec.zscore_enabled:
                z_anom = self._check_zscore(spec, value)
                if z_anom is not None:
                    anomalies.append(z_anom)

            roc_anom = self._check_roc(spec, value)
            if roc_anom is not None:
                anomalies.append(roc_anom)

        if self.ml is not None and not late:
            sun = getattr(reading, "sun_fraction", None)
            if sun is not None:
                values["sun_fraction"] = float(sun)
            anomalies.extend(self.ml.add(now, values))

        return anomalies

    # -- rule ----------------------------------------------------------------- #
    def _check_rule(self, spec: SensorSpec, value: float, reading) -> Optional[Anomaly]:
        if spec.sunlit_min is not None:
            if float(getattr(reading, "sun_fraction", 0.0)) < spec.sunlit_min:
                return None

        severity: Optional[str] = None
        if spec.direction is Direction.HIGH_BAD:
            if spec.crit is not None and value >= spec.crit:
                severity = "critical"
            elif spec.warn is not None and value >= spec.warn:
                severity = "warning"
        else:  # LOW_BAD
            if spec.crit is not None and value <= spec.crit:
                severity = "critical"
            elif spec.warn is not None and value <= spec.warn:
                severity = "warning"

        if severity is None:
            return None

        limit = spec.crit if severity == "critical" else spec.warn
        op = ">=" if spec.direction is Direction.HIGH_BAD else "<="

        if spec.direction is Direction.HIGH_BAD:
            expected = (spec.warn, spec.crit)  # type: ignore[assignment]
        else:
            expected = (spec.crit, spec.warn)  # type: ignore[assignment]

        return Anomaly(
            sensor=spec.name,
            value=value,
            detector_type=DetectorType.RULE,
            severity_hint=severity,
            subsystem=spec.subsystem,
            description=f"{spec.name}={value:.3f} {op} {limit} (rule threshold, {severity})",
            expected_range=expected,
            confidence=0.99,
        )

    # -- z-score -------------------------------------------------------------- #
    def _check_zscore(self, spec: SensorSpec, value: float) -> Optional[Anomaly]:
        buf = self.z_buffers[spec.name]
        if len(buf) < ZSCORE_MIN_SAMPLES:
            self._z_hits[spec.name] = 0
            return None

        z = buf.zscore_last()
        if z is None:
            self._z_hits[spec.name] = 0
            return None

        if abs(z) < Z_THRESH:
            self._z_hits[spec.name] = 0
            return None

        self._z_hits[spec.name] += 1
        if self._z_hits[spec.name] < spec.min_consecutive:
            return None
        self._z_hits[spec.name] = 0

        stats = buf.mean_std()
        mean, std = stats if stats else (None, None)
        severity = "critical" if abs(z) >= 5.0 else "warning"
        expected = (mean - Z_THRESH * std, mean + Z_THRESH * std) if mean is not None else None
        suffix = " (sunlit model residual)" if spec.model_residual else ""
        return Anomaly(
            sensor=spec.name,
            value=value,
            detector_type=DetectorType.ZSCORE,
            severity_hint=severity,
            subsystem=spec.subsystem,
            description=(
                f"{spec.name} z={z:+.1f} over {int(spec.z_window_sec)}s window "
                f"(3-sigma breach x{spec.min_consecutive}){suffix}"
            ),
            baseline=mean,
            expected_range=expected,
            confidence=min(0.99, 0.60 + 0.08 * min(abs(z), 5.0)),
        )

    # -- rate of change ------------------------------------------------------- #
    def _check_roc(self, spec: SensorSpec, value: float) -> Optional[Anomaly]:
        if spec.roc_thresh is None:
            return None
        buf = self.roc_buffers[spec.name]
        if len(buf) < max(5, int(spec.roc_window_sec * 0.6)):
            self._roc_hits[spec.name] = 0
            return None

        slope = buf.slope(spec.roc_window_sec)
        if slope is None:
            self._roc_hits[spec.name] = 0
            return None

        if abs(slope) < spec.roc_thresh:
            self._roc_hits[spec.name] = 0
            return None

        self._roc_hits[spec.name] += 1
        if self._roc_hits[spec.name] < spec.min_consecutive:
            return None
        self._roc_hits[spec.name] = 0

        severity = "critical" if abs(slope) >= 2 * spec.roc_thresh else "warning"
        return Anomaly(
            sensor=spec.name,
            value=value,
            detector_type=DetectorType.RATE,
            severity_hint=severity,
            subsystem=spec.subsystem,
            description=(
                f"{spec.name} rate {slope:+.3f}/s over {int(spec.roc_window_sec)}s "
                f"(threshold {spec.roc_thresh}/s)"
            ),
            confidence=0.85,
            extra={"slope": slope, "roc_thresh": spec.roc_thresh},
        )
