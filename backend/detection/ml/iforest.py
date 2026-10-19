"""Isolation Forest model over windowed telemetry features.

Train on *normal* (nominal) windowed features only - no labels - with
scikit-learn's ``IsolationForest``. Anomalies are points the ensemble
isolates with fewer random splits than anything seen in training.

The artifact persisted by :meth:`IsolationForestModel.save` contains:

* the fitted estimator;
* the canonical feature order used at training time;
* the calibrated decision threshold (a point is anomalous when
  ``decision_function(x) < threshold``; higher decision = more normal);
* per-feature mean/std and per-sensor raw mean/std for explainability
  (feature z-scores -> contributing sensors -> expected_range);
* provenance metadata (training source, seeds, sizes, library versions).

Retrain with ``backend/tools/train_iforest.py``; the artifact is data, not
source, and is regenerated rather than hand-edited.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

#: Default artifact location (next to this package; produced by the train script).
MODEL_PATH = os.path.join(os.path.dirname(__file__), "artifacts", "iforest.joblib")

_FORMAT_VERSION = 1


@dataclass
class ModelMetadata:
    created_at: str
    format_version: int = _FORMAT_VERSION
    n_train_samples: int = 0
    n_features: int = 0
    n_estimators: int = 0
    random_state: int = 0
    threshold_quantile: float = 0.0
    threshold: float = 0.0
    decision_min: float = 0.0
    decision_max: float = 0.0
    provenance: str = ""
    sklearn_version: str = ""
    numpy_version: str = ""


class IsolationForestModel:
    """Trained Isolation Forest + calibration + explainability statistics."""

    def __init__(
        self,
        estimator: IsolationForest,
        feature_names: Sequence[str],
        threshold: float,
        feature_mean: np.ndarray,
        feature_std: np.ndarray,
        sensor_stats: Dict[str, Tuple[float, float]],
        metadata: ModelMetadata,
    ) -> None:
        self.estimator = estimator
        self.feature_names = list(feature_names)
        self.threshold = float(threshold)
        self.feature_mean = np.asarray(feature_mean, dtype=float)
        self.feature_std = np.asarray(feature_std, dtype=float)
        #: sensor -> (train mean, train std) of the raw signal, for expected_range
        self.sensor_stats = {k: (float(v[0]), float(v[1])) for k, v in sensor_stats.items()}
        self.metadata = metadata

    # -- training ------------------------------------------------------------ #
    @classmethod
    def train(
        cls,
        X: np.ndarray,
        feature_names: Sequence[str],
        *,
        sensor_values: Optional[Dict[str, np.ndarray]] = None,
        threshold_quantile: float = 0.001,
        n_estimators: int = 200,
        random_state: int = 42,
        provenance: str = "",
    ) -> "IsolationForestModel":
        """Fit on a (n_samples, n_features) nominal matrix.

        ``threshold_quantile`` is the quantile of *training* decision scores
        below which a point is considered anomalous (0.001 = the most isolated
        0.1 % of the nominal training set). Evaluation scripts may re-calibrate
        this against a held-out nominal stream.
        """
        X = np.asarray(X, dtype=float)
        if X.ndim != 2 or X.shape[0] < 100:
            raise ValueError(f"need >=100 nominal windows, got {X.shape!r}")
        if X.shape[1] != len(feature_names):
            raise ValueError("feature_names does not match X column count")

        estimator = IsolationForest(
            n_estimators=n_estimators,
            max_samples="auto",
            contamination="auto",
            random_state=random_state,
            n_jobs=-1,
        )
        estimator.fit(X)
        decisions = estimator.decision_function(X)
        threshold = float(np.quantile(decisions, threshold_quantile))

        feature_mean = X.mean(axis=0)
        feature_std = X.std(axis=0, ddof=1) if X.shape[0] > 1 else np.zeros(X.shape[1])
        feature_std = np.where(feature_std < 1e-12, 1.0, feature_std)

        stats: Dict[str, Tuple[float, float]] = {}
        if sensor_values:
            for name, values in sensor_values.items():
                arr = np.asarray(values, dtype=float)
                std = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
                stats[name] = (float(arr.mean()), max(std, 1e-12))

        metadata = ModelMetadata(
            created_at=datetime.now(timezone.utc).isoformat(),
            n_train_samples=int(X.shape[0]),
            n_features=int(X.shape[1]),
            n_estimators=n_estimators,
            random_state=random_state,
            threshold_quantile=threshold_quantile,
            threshold=threshold,
            decision_min=float(decisions.min()),
            decision_max=float(decisions.max()),
            provenance=provenance,
            sklearn_version=_pkg_version("sklearn"),
            numpy_version=_pkg_version("numpy"),
        )
        return cls(estimator, feature_names, threshold, feature_mean, feature_std, stats, metadata)

    def recalibrate(self, X: np.ndarray, threshold_quantile: float) -> float:
        """Re-derive the decision threshold from a nominal matrix (e.g. a
        held-out calibration stream) and return it."""
        decisions = self.estimator.decision_function(np.asarray(X, dtype=float))
        self.threshold = float(np.quantile(decisions, threshold_quantile))
        self.metadata.threshold = self.threshold
        self.metadata.threshold_quantile = float(threshold_quantile)
        self.metadata.decision_min = float(decisions.min())
        self.metadata.decision_max = float(decisions.max())
        return self.threshold

    # -- inference ------------------------------------------------------------ #
    def decision(self, x: Sequence[float]) -> float:
        """Raw Isolation Forest decision (higher = more normal)."""
        arr = np.asarray(x, dtype=float).reshape(1, -1)
        return float(self.estimator.decision_function(arr)[0])

    def is_anomaly(self, x: Sequence[float]) -> bool:
        return self.decision(x) < self.threshold

    def anomaly_score(self, x: Sequence[float]) -> float:
        """Normalised score in [0, 1]: 0 at the threshold, 1 at the most
        anomalous decision observed in training. Higher = more anomalous."""
        d = self.decision(x)
        span = self.threshold - self.metadata.decision_min
        if span <= 1e-12:
            return 1.0 if d < self.threshold else 0.0
        return float(min(1.0, max(0.0, (self.threshold - d) / span)))

    def feature_z(self, x: Sequence[float]) -> np.ndarray:
        """Per-feature z-score vs the training distribution."""
        arr = np.asarray(x, dtype=float)
        return (arr - self.feature_mean) / self.feature_std

    def contributing_sensors(self, x: Sequence[float], top_k: int = 3) -> List[str]:
        """Sensors whose windowed features deviate most from training.

        Feature-level |z| is summed per sensor (mean/std/slope together) and
        the sensors are ranked. Context series (``sun_fraction``) are excluded
        - they explain illumination, not a fault.
        """
        z = np.abs(self.feature_z(x))
        per_sensor: Dict[str, float] = {}
        for i, name in enumerate(self.feature_names):
            sensor = name.split("__", 1)[0]
            # Context-only series (e.g. sun_fraction) are absent from
            # sensor_stats and explain illumination, not a fault.
            if sensor not in self.sensor_stats:
                continue
            per_sensor[sensor] = per_sensor.get(sensor, 0.0) + float(z[i])
        ranked = sorted(per_sensor.items(), key=lambda kv: kv[1], reverse=True)
        if not ranked or ranked[0][1] <= 0.0:
            return []
        cutoff = 0.25 * ranked[0][1]
        return [name for name, score in ranked[:top_k] if score >= cutoff]

    def expected_range(self, sensor: str, sigma: float = 2.0) -> Optional[Tuple[float, float]]:
        stats = self.sensor_stats.get(sensor)
        if stats is None:
            return None
        mean, std = stats
        return (mean - sigma * std, mean + sigma * std)

    # -- persistence ---------------------------------------------------------- #
    def save(self, path: str = MODEL_PATH) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        joblib.dump(
            {
                "format_version": _FORMAT_VERSION,
                "estimator": self.estimator,
                "feature_names": self.feature_names,
                "threshold": self.threshold,
                "feature_mean": self.feature_mean,
                "feature_std": self.feature_std,
                "sensor_stats": self.sensor_stats,
                "metadata": asdict(self.metadata),
            },
            path,
        )
        return path

    @classmethod
    def load(cls, path: str = MODEL_PATH) -> "IsolationForestModel":
        blob = joblib.load(path)
        version = blob.get("format_version")
        if version != _FORMAT_VERSION:
            raise ValueError(f"unsupported model format {version!r} in {path}")
        return cls(
            estimator=blob["estimator"],
            feature_names=blob["feature_names"],
            threshold=blob["threshold"],
            feature_mean=blob["feature_mean"],
            feature_std=blob["feature_std"],
            sensor_stats=blob["sensor_stats"],
            metadata=ModelMetadata(**blob["metadata"]),
        )


def load_model(path: str = MODEL_PATH) -> Optional[IsolationForestModel]:
    """Load the artifact if present; None when it has not been trained yet."""
    if not os.path.isfile(path):
        return None
    return IsolationForestModel.load(path)


def _pkg_version(name: str) -> str:
    try:
        module = __import__(name)
        return str(getattr(module, "__version__", "unknown"))
    except Exception:  # noqa: BLE001 - version stamp is best-effort metadata
        return "unknown"
