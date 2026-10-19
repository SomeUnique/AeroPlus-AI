"""Machine-learning detection layer (Isolation Forest).

Public API:
    WindowedFeatureExtractor - rolling mean / std / slope per sensor
    IsolationForestModel     - trained on nominal telemetry, saved to disk
    MLStreamDetector         - streaming wrapper: windows -> score -> Anomaly

The model file (``artifacts/iforest.joblib``) is produced by
``backend/tools/train_iforest.py`` and is NOT checked in as source: retrain
with that script (it takes a couple of minutes). If the artifact is missing
the ML detector stays silent and the rule / z-score / rate layers still run.
"""

from detection.ml.features import WINDOWED_FEATURE_NAMES, WindowedFeatureExtractor
from detection.ml.iforest import MODEL_PATH, IsolationForestModel, load_model
from detection.ml.stream import MLStreamDetector

__all__ = [
    "WindowedFeatureExtractor",
    "WINDOWED_FEATURE_NAMES",
    "IsolationForestModel",
    "MODEL_PATH",
    "load_model",
    "MLStreamDetector",
]
