"""Agent (Decide -> Act) subsystem.

Public API:
    AgentService   - consume detection payloads, emit thinking/action/report
    build_thinking - pure playbook -> reasoning trace
    build_actions  - pure playbook -> proposed actions
    select_playbook- pick the per-subsystem procedure
"""

from agent.agent import (  # noqa: F401
    IncidentState,
    build_actions,
    build_report,
    build_thinking,
    incident_key,
    severity_rank,
    template_values,
)
from agent.playbooks import PLAYBOOKS, ActionSpec, Playbook, select_playbook  # noqa: F401
from agent.service import AgentService  # noqa: F401

__all__ = [
    "AgentService",
    "IncidentState",
    "build_thinking",
    "build_actions",
    "build_report",
    "incident_key",
    "severity_rank",
    "template_values",
    "PLAYBOOKS",
    "Playbook",
    "ActionSpec",
    "select_playbook",
]
