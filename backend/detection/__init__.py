"""Anomaly detection (Detect) subsystem.

Public API:
    DetectionService - feed telemetry, get back WS payloads (anomalies + status)
    Detector         - low-level rule/z-score/rate/iforest detector (per-sensor)
    IncidentManager  - groups anomalies by subsystem, debounces, tracks status
    ML layer         - detection.ml: windowed features + Isolation Forest
"""

from detection.config import SENSORS, DetectorType, Subsystem, SystemStatus, Z_THRESH  # noqa: F401
from detection.detector import Detector  # noqa: F401
from detection.incidents import IncidentManager  # noqa: F401
from detection.ml import IsolationForestModel, MLStreamDetector, load_model  # noqa: F401
from detection.models import Anomaly, Incident  # noqa: F401
from detection.service import DetectionService  # noqa: F401

__all__ = [
    "DetectionService",
    "Detector",
    "IncidentManager",
    "IsolationForestModel",
    "MLStreamDetector",
    "load_model",
    "Anomaly",
    "Incident",
    "SENSORS",
    "DetectorType",
    "Subsystem",
    "SystemStatus",
    "Z_THRESH",
]
