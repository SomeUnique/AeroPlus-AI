"""Anomaly representation + helpers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from detection.config import DetectorType, Subsystem


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_anom_id() -> str:
    return f"anom_{uuid.uuid4().hex[:12]}"


def new_incident_id() -> str:
    return f"inc_{uuid.uuid4().hex[:12]}"


@dataclass
class Anomaly:
    sensor: str = ""
    value: float = 0.0
    detector_type: DetectorType = DetectorType.RULE
    severity_hint: str = "info"      # info | warning | critical
    subsystem: Subsystem = Subsystem.UNKNOWN
    description: str = ""
    expected_range: Optional[Tuple[float, float]] = None
    timestamp: datetime = field(default_factory=_utcnow)
    confidence: float = 0.9
    anomaly_id: str = field(default_factory=new_anom_id)
    incident_id: Optional[str] = None
    baseline: Optional[float] = None
    extra: Dict = field(default_factory=dict)
    #: ML score (0..1, higher = more anomalous); None for rule/statistical detectors
    anomaly_score: Optional[float] = None
    #: sensors contributing most to an ML anomaly (explainability)
    contributing_sensors: List[str] = field(default_factory=list)


@dataclass
class Incident:
    incident_id: str = field(default_factory=new_incident_id)
    subsystem: Subsystem = Subsystem.UNKNOWN
    sensor: str = ""
    severity: str = "info"
    anomalies: List[Anomaly] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    resolved: bool = False
    last_broadcast_at: datetime = field(default_factory=_utcnow)
    description: str = ""
