"""Agent core: turn an anomaly + context into a reasoning trace and actions.

This module is pure and synchronous (no I/O, no clock, no sleeps) so the Decide
policy can be unit-tested directly. :mod:`agent.service` wraps it with the async
event loop that paces emission over the WebSocket.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List

from agent.playbooks import ActionSpec, Playbook, select_playbook
from models.schemas import (
    ActionStatus,
    AgentActionMessage,
    AgentThinkingMessage,
    AgentPhase,
    AnomalyMessage,
    ReportReadyMessage,
    ReportSection,
    Severity,
)

_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def severity_rank(value: str) -> int:
    return _SEVERITY_RANK.get(value, 0)


def incident_key(anomaly: AnomalyMessage) -> str:
    return anomaly.incident_id or f"{anomaly.subsystem or 'Unknown'}:{anomaly.sensor.value}"


def _fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def template_values(anomaly: AnomalyMessage, context: Dict[str, Any] | None) -> Dict[str, str]:
    context = context or {}
    sun = float(context.get("sun_fraction", 1.0))
    return {
        "sensor": anomaly.sensor.value,
        "value": _fmt(anomaly.value, 3),
        "lo": _fmt(anomaly.expected_min),
        "hi": _fmt(anomaly.expected_max),
        "detector": anomaly.detector_type.value if anomaly.detector_type else "rule",
        "severity": anomaly.severity.value,
        "confidence": f"{anomaly.confidence:.0%}",
        "subsystem": anomaly.subsystem or "Unknown",
        "payload_mode": str(context.get("payload_mode", "unknown")),
        "sun_state": "sunlit" if sun >= 0.5 else "eclipsed",
    }


def build_thinking(
    anomaly: AnomalyMessage, context: Dict[str, Any] | None, playbook: Playbook
) -> List[AgentThinkingMessage]:
    values = template_values(anomaly, context)
    steps: List[AgentThinkingMessage] = []
    first, *rest = playbook.steps
    for phase, template in [first, (AgentPhase.REASON, playbook.severity_note), *rest]:
        steps.append(
            AgentThinkingMessage(
                step=len(steps) + 1,
                phase=phase,
                content=template.format(**values),
                inputs={
                    "sensor": anomaly.sensor.value,
                    "value": anomaly.value,
                    "severity": anomaly.severity.value,
                    "detector": anomaly.detector_type.value if anomaly.detector_type else None,
                    "incident_id": anomaly.incident_id,
                },
            )
        )
    return steps


def build_actions(
    anomaly: AnomalyMessage, context: Dict[str, Any] | None, playbook: Playbook
) -> List[AgentActionMessage]:
    values = template_values(anomaly, context)
    actions: List[AgentActionMessage] = []
    for spec in playbook.actions:
        actions.append(
            AgentActionMessage(
                action=spec.action,
                target=spec.target,
                params=dict(spec.params or {}),
                rationale=spec.rationale.format(**values),
                status=ActionStatus.PROPOSED,
            )
        )
    return actions


@dataclass
class IncidentState:
    """Everything the agent knows about one incident, for its report."""

    incident_id: str
    subsystem: str
    playbook: Playbook
    severity: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    anomaly_ids: List[str] = field(default_factory=list)
    actions: List[AgentActionMessage] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    thinking_steps: int = 0

    def note_anomaly(self, anomaly: AnomalyMessage) -> bool:
        """Record an anomaly; returns True if the severity escalated."""
        if anomaly.anomaly_id not in self.anomaly_ids:
            self.anomaly_ids.append(anomaly.anomaly_id)
        escalated = severity_rank(anomaly.severity.value) > severity_rank(self.severity)
        if escalated:
            self.severity = anomaly.severity.value
        return escalated


def build_report(state: IncidentState, ended_at: datetime | None = None) -> ReportReadyMessage:
    ended_at = ended_at or datetime.now(timezone.utc)
    sections = [
        ReportSection(
            heading="Timeline",
            body=(
                f"Incident {state.incident_id} on {state.subsystem} opened "
                f"{state.started_at.isoformat()} and closed {ended_at.isoformat()} "
                f"({ended_at - state.started_at} elapsed). "
                f"{len(state.anomaly_ids)} anomalous frame(s) observed."
            ),
        ),
        ReportSection(heading="Root cause", body=state.playbook.diagnosis),
        ReportSection(
            heading="Actions taken",
            body=(
                "; ".join(
                    f"{a.action} ({a.target}) [{a.status.value}]"
                    for a in state.actions
                )
                or "No corrective action required."
            ),
        ),
        ReportSection(
            heading="Outcome",
            body=f"{state.playbook.outcome} Subsystem {state.subsystem} returned to NOMINAL.",
        ),
    ]
    return ReportReadyMessage(
        title=f"{state.playbook.title} - {state.subsystem}",
        summary=(
            f"{state.playbook.diagnosis} The co-pilot issued "
            f"{len(state.actions)} action(s) and the {state.subsystem} subsystem "
            f"returned to NOMINAL."
        ),
        severity=Severity(state.severity),
        sections=sections,
        related_anomaly_ids=list(state.anomaly_ids),
        generated_at=ended_at,
    )
