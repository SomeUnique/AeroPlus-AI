"""Detection service: wires the detector + incident manager to the wire contract.

The service is deliberately transport-agnostic: :meth:`process` returns payload
models (``AnomalyMessage`` / ``StatusUpdateMessage``) and the WebSocket layer
wraps them in envelopes. This keeps detection unit-testable without a server.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Union

from detection.config import DetectorType, Subsystem, SystemStatus
from detection.detector import Detector
from detection.incidents import IncidentManager, StatusChange
from detection.models import Anomaly
from models.schemas import (
    AnomalyKind,
    AnomalyMessage,
    DetectorKind,
    Sensor,
    Severity,
    StatusSource,
    StatusUpdateMessage,
)

Payload = Union[AnomalyMessage, StatusUpdateMessage]

_KIND_BY_DETECTOR = {
    DetectorType.RULE: AnomalyKind.OUT_OF_RANGE,
    DetectorType.ZSCORE: AnomalyKind.DRIFT,
    DetectorType.RATE: AnomalyKind.RATE_OF_CHANGE,
    DetectorType.CORRELATED: AnomalyKind.CORRELATED,
    DetectorType.IFOREST: AnomalyKind.ML_ANOMALY,
}

_STATUS_LEVEL = {
    SystemStatus.NOMINAL: Severity.INFO,
    SystemStatus.WARNING: Severity.WARNING,
    SystemStatus.CRITICAL: Severity.CRITICAL,
}


def _kind_for(anomaly: Anomaly) -> AnomalyKind:
    # RF drop has a dedicated kind; everything else maps from the detector.
    if anomaly.sensor == "signal_strength" and anomaly.detector_type in (
        DetectorType.ZSCORE,
        DetectorType.RATE,
    ):
        return AnomalyKind.SIGNAL_DEGRADATION
    return _KIND_BY_DETECTOR.get(anomaly.detector_type, AnomalyKind.OUT_OF_RANGE)


class DetectionService:
    """Owns detection state and translates it into WS payloads."""

    def __init__(self) -> None:
        self.detector = Detector()
        self.incidents = IncidentManager()
        self.anomalies_raised = 0

    # -- lifecycle ------------------------------------------------------------ #
    def reset(self) -> None:
        self.detector.reset()
        self.incidents.reset()
        self.anomalies_raised = 0

    # -- main entry point ----------------------------------------------------- #
    def process(self, reading, now: Optional[float] = None) -> List[Payload]:
        clock = now if now is not None else reading.timestamp.timestamp()
        anomalies = self.detector.feed(reading, clock)
        if anomalies:
            self.anomalies_raised += len(anomalies)

        emitted, changes = self.incidents.feed(anomalies)
        payloads: List[Payload] = [self._to_anomaly_message(a) for a in emitted]
        payloads.extend(self._to_status_message(c) for c in changes)
        return payloads

    # -- mapping -------------------------------------------------------------- #
    @staticmethod
    def _to_anomaly_message(a: Anomaly) -> AnomalyMessage:
        expected_min = a.expected_range[0] if a.expected_range else None
        expected_max = a.expected_range[1] if a.expected_range else None
        try:
            detector_kind = DetectorKind(a.detector_type.value)
        except ValueError:
            detector_kind = DetectorKind.RULE
        return AnomalyMessage(
            anomaly_id=a.anomaly_id,
            sensor=Sensor(a.sensor),
            kind=_kind_for(a),
            severity=Severity(a.severity_hint),
            value=a.value,
            expected_min=expected_min,
            expected_max=expected_max,
            baseline=a.baseline,
            confidence=a.confidence,
            detected_at=a.timestamp,
            description=a.description,
            detector_type=detector_kind,
            subsystem=a.subsystem.value,
            incident_id=a.incident_id,
            anomaly_score=a.anomaly_score,
            contributing_sensors=list(a.contributing_sensors),
        )

    @staticmethod
    def _to_status_message(change: StatusChange) -> StatusUpdateMessage:
        subsystem, previous, current, exemplar = change
        level = _STATUS_LEVEL[current]
        if current is SystemStatus.NOMINAL:
            message = f"{subsystem.value} subsystem recovered to NOMINAL (was {previous.value})."
        else:
            detail = f" - {exemplar.description}" if exemplar else ""
            message = f"{subsystem.value} subsystem {current.value} (was {previous.value}){detail}"
        return StatusUpdateMessage(
            source=StatusSource.DETECTION,
            level=level,
            status=f"{subsystem.value}_{current.value}",
            message=message,
        )

    # -- introspection (used by /health and tests) ---------------------------- #
    def status_snapshot(self) -> Dict[str, str]:
        return self.incidents.snapshot()

    def recovery_notices(self) -> List[StatusUpdateMessage]:
        """Synthesize NOMINAL notices for every non-nominal subsystem.

        Called on operator reset so downstream consumers (the agent) can close
        any open incidents and emit their reports before detection state clears.
        """
        notices: List[StatusUpdateMessage] = []
        for subsystem, status in self.incidents.status.items():
            if status is SystemStatus.NOMINAL:
                continue
            notices.append(
                StatusUpdateMessage(
                    source=StatusSource.DETECTION,
                    level=Severity.INFO,
                    status=f"{subsystem.value}_{SystemStatus.NOMINAL.value}",
                    message=(
                        f"{subsystem.value} subsystem recovered to NOMINAL "
                        f"(operator reset; was {status.value})."
                    ),
                )
            )
        return notices
