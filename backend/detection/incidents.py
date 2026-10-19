"""Incident grouping + debouncing + subsystem status tracking."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from detection.config import (
    INCIDENT_DEBOUNCE_SEC,
    INCIDENT_EXPIRY_SEC,
    MAX_INCIDENT_ANOMALIES,
    STATUS_SUBSYSTEMS,
    Subsystem,
    SystemStatus,
)
from detection.models import Anomaly, Incident

_SEV_ORDER = {"info": 0, "warning": 1, "critical": 2}
_STATUS_ORDER = {
    SystemStatus.NOMINAL: 0,
    SystemStatus.WARNING: 1,
    SystemStatus.CRITICAL: 2,
}

#: (subsystem, previous, current, exemplar anomaly) - emitted to the WS layer
StatusChange = Tuple[Subsystem, SystemStatus, SystemStatus, Optional[Anomaly]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _max_sev(a: str, b: str) -> str:
    return a if _SEV_ORDER.get(a, 0) >= _SEV_ORDER.get(b, 0) else b


def _status_for(severity: str) -> SystemStatus:
    if severity == "critical":
        return SystemStatus.CRITICAL
    if severity == "warning":
        return SystemStatus.WARNING
    return SystemStatus.NOMINAL


class IncidentManager:
    """Groups anomalies by subsystem; debounces broadcasts; tracks statuses."""

    def __init__(
        self,
        debounce_sec: float = INCIDENT_DEBOUNCE_SEC,
        expiry_sec: float = INCIDENT_EXPIRY_SEC,
    ) -> None:
        self.debounce_sec = debounce_sec
        self.expiry_sec = expiry_sec
        self.incidents: Dict[Subsystem, Incident] = {}
        self.status: Dict[Subsystem, SystemStatus] = {
            s: SystemStatus.NOMINAL for s in STATUS_SUBSYSTEMS
        }

    def feed(self, anomalies: List[Anomaly]) -> Tuple[List[Anomaly], List[StatusChange]]:
        now = _utcnow()
        emitted: List[Anomaly] = []

        for anom in anomalies:
            sub = anom.subsystem
            inc = self.incidents.get(sub)
            is_new = inc is None
            if inc is None:
                inc = Incident(subsystem=sub, sensor=anom.sensor)
                self.incidents[sub] = inc

            anom.incident_id = inc.incident_id
            escalated = _SEV_ORDER.get(anom.severity_hint, 0) > _SEV_ORDER.get(inc.severity, 0)
            quiet_for = (now - inc.last_broadcast_at).total_seconds()
            if is_new or escalated or quiet_for >= self.debounce_sec:
                emitted.append(anom)
                inc.last_broadcast_at = now

            inc.anomalies.append(anom)
            if len(inc.anomalies) > MAX_INCIDENT_ANOMALIES:
                del inc.anomalies[0: len(inc.anomalies) - MAX_INCIDENT_ANOMALIES]
            inc.severity = _max_sev(inc.severity, anom.severity_hint)
            inc.updated_at = now
            inc.resolved = False

        # Expire incidents that have gone quiet.
        for inc in self.incidents.values():
            if not inc.resolved and (now - inc.updated_at).total_seconds() >= self.expiry_sec:
                inc.resolved = True

        # Recompute subsystem statuses (single pass).
        changes: List[StatusChange] = []
        for sub in STATUS_SUBSYSTEMS:
            inc = self.incidents.get(sub)
            fresh = inc is not None and not inc.resolved
            target = _status_for(inc.severity) if fresh else SystemStatus.NOMINAL
            current = self.status[sub]
            if target != current:
                self.status[sub] = target
                exemplar = inc.anomalies[-1] if inc and inc.anomalies else None
                changes.append((sub, current, target, exemplar))

        return emitted, changes

    def reset(self) -> None:
        self.incidents.clear()
        self.status = {s: SystemStatus.NOMINAL for s in STATUS_SUBSYSTEMS}

    def snapshot(self) -> Dict[str, str]:
        return {s.value: st.value for s, st in self.status.items()}

    def active_incidents(self) -> List[Incident]:
        return [i for i in self.incidents.values() if not i.resolved]
