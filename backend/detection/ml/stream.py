"""Streaming wrapper: windowed features -> Isolation Forest -> Anomaly.

Operates post-link on telemetry that has already reached the ground: it never
touches the RF path, it only consumes decoded readings in (mostly) mission
time order. Windows are time-bounded, so missing packets shrink the window
rather than desynchronising it, and a persistence gate requires
``min_consecutive`` consecutive flagged windows before an anomaly is
emitted - that is what keeps one-off noise or a single late packet from
raising an alert.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from detection.config import SENSORS, DetectorType, Subsystem
from detection.ml.features import WindowedFeatureExtractor, windowed_feature_names
from detection.ml.iforest import IsolationForestModel
from detection.ml.physics import DERIVED_SERIES, augment_values
from detection.models import Anomaly

#: feature window used by the CubeSat model (seconds of mission time)
ML_WINDOW_SEC = 20.0
ML_MIN_SAMPLES = 8
ML_MIN_SPAN_SEC = 6.0
ML_MIN_CONSECUTIVE = 3
#: score at/above which an ML anomaly is raised as critical rather than warning
ML_CRITICAL_SCORE = 0.95

CONTEXT_NAMES = ("sun_fraction",)
#: extractor series: the seven raw sensors plus the physics-derived residuals
ML_SERIES_NAMES = tuple(SENSORS.keys()) + DERIVED_SERIES


def ml_feature_names() -> list:
    """Canonical feature order shared by training and serving."""
    return windowed_feature_names(ML_SERIES_NAMES, CONTEXT_NAMES)


class MLStreamDetector:
    """Stateful streaming Isolation Forest detector."""

    def __init__(
        self,
        model: IsolationForestModel,
        *,
        series_names: Optional[List[str]] = None,
        context_names: Optional[List[str]] = None,
        window_sec: float = ML_WINDOW_SEC,
        min_consecutive: int = ML_MIN_CONSECUTIVE,
    ) -> None:
        self.model = model
        self.series_names = list(series_names or ML_SERIES_NAMES)
        self.context_names = list(context_names if context_names is not None else CONTEXT_NAMES)
        self.min_consecutive = max(1, int(min_consecutive))
        self.extractor = WindowedFeatureExtractor(
            self.series_names,
            self.context_names,
            window_sec=window_sec,
            min_samples=ML_MIN_SAMPLES,
            min_span_sec=ML_MIN_SPAN_SEC,
        )
        self._hits = 0
        self.windows_scored = 0
        self.anomalies_raised = 0

    def reset(self) -> None:
        self.extractor.reset()
        self._hits = 0
        self.windows_scored = 0
        self.anomalies_raised = 0

    def add(self, t: float, values: Dict[str, float]) -> List[Anomaly]:
        """Feed one ground-received reading; return anomalies (maybe empty)."""
        self.extractor.add(t, augment_values(values))
        features = self.extractor.feature_vector()
        if features is None:
            return []

        self.windows_scored += 1
        if not self.model.is_anomaly(features):
            self._hits = 0
            return []

        self._hits += 1
        if self._hits < self.min_consecutive:
            return []

        self._hits = 0
        self.anomalies_raised += 1
        return [self._to_anomaly(features)]

    # -- internals ------------------------------------------------------------ #
    def _to_anomaly(self, features: List[float]) -> Anomaly:
        score = self.model.anomaly_score(features)
        contributors = self.model.contributing_sensors(features)
        primary = contributors[0] if contributors else self.series_names[0]
        spec = SENSORS.get(primary)
        subsystem = spec.subsystem if spec is not None else Subsystem.UNKNOWN
        last_value = self.extractor.last_values.get(primary, float("nan"))
        expected = self.model.expected_range(primary)

        severity = "critical" if score >= ML_CRITICAL_SCORE else "warning"
        names = ", ".join(contributors) if contributors else "n/a"
        return Anomaly(
            sensor=primary,
            value=last_value,
            detector_type=DetectorType.IFOREST,
            severity_hint=severity,
            subsystem=subsystem,
            description=(
                f"iforest: windowed telemetry deviates from the nominal model "
                f"(score {score:.2f}); top contributing sensors: {names}"
            ),
            expected_range=expected,
            baseline=self.model.sensor_stats.get(primary, (None, None))[0],
            confidence=min(0.99, max(0.5, score)),
            anomaly_score=round(score, 4),
            contributing_sensors=contributors,
            extra={
                "ml_score": round(score, 4),
                "ml_threshold": self.model.threshold,
                "contributors": contributors,
            },
        )
