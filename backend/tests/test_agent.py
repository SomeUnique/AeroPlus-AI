from __future__ import annotations

import asyncio
from datetime import datetime

from agent.agent import (
    IncidentState,
    build_actions,
    build_report,
    build_thinking,
    incident_key,
    severity_rank,
    template_values,
)
from agent.playbooks import PLAYBOOKS, select_playbook
from agent.service import AgentService
from detection.config import Subsystem, SystemStatus
from detection.service import DetectionService
from models.schemas import (
    ActionStatus,
    AnomalyKind,
    AnomalyMessage,
    AgentPhase,
    AgentThinkingMessage,
    AgentActionMessage,
    DetectorKind,
    PayloadMode,
    ReportReadyMessage,
    Sensor,
    Severity,
    StatusSource,
    StatusUpdateMessage,
    TelemetryReading,
)


def reading(**overrides) -> TelemetryReading:
    base = dict(
        timestamp=datetime.now(),
        battery_voltage=27.6,
        battery_temp=30.0,
        solar_current=6.5,
        bus_temp=25.0,
        signal_strength=-75.0,
        gyro_rate=0.003,
        payload_power=2.5,
        in_eclipse=False,
        sun_fraction=1.0,
        payload_mode=PayloadMode.IDLE,
    )
    base.update(overrides)
    return TelemetryReading(**base)


def ctx(sun_fraction: float = 1.0, payload_mode: str = "idle") -> dict:
    return {"sun_fraction": sun_fraction, "payload_mode": payload_mode}


def anomaly(
    *,
    sensor: Sensor = Sensor.BATTERY_TEMP,
    subsystem: str = "Thermal",
    severity: Severity = Severity.WARNING,
    value: float = 52.0,
    incident_id: str = "inc-1",
    anomaly_id: str | None = None,
) -> AnomalyMessage:
    kwargs = {}
    if anomaly_id is not None:
        kwargs["anomaly_id"] = anomaly_id
    return AnomalyMessage(
        sensor=sensor,
        kind=AnomalyKind.OUT_OF_RANGE,
        severity=severity,
        value=value,
        expected_min=0.0,
        expected_max=50.0,
        confidence=0.9,
        description="test anomaly",
        detector_type=DetectorKind.RULE,
        subsystem=subsystem,
        incident_id=incident_id,
        **kwargs,
    )


# -- playbooks / planning ----------------------------------------------------- #
def test_select_playbook_maps_subsystems_and_falls_back():
    assert select_playbook("Thermal") is PLAYBOOKS["Thermal"]
    assert select_playbook("Comms") is PLAYBOOKS["Comms"]
    assert select_playbook("nope").subsystem == "Unknown"
    assert select_playbook(None).subsystem == "Unknown"


def test_incident_key_prefers_incident_id():
    assert incident_key(anomaly(incident_id="inc-7")) == "inc-7"
    assert incident_key(anomaly(incident_id=None)).startswith("Thermal:")


def test_severity_rank_orders_levels():
    assert severity_rank("critical") > severity_rank("warning") > severity_rank("info")
    assert severity_rank("bogus") == 0


def test_build_thinking_emits_ordered_trace_with_no_leftover_tokens():
    a = anomaly()
    steps = build_thinking(a, ctx(), select_playbook("Thermal"))
    assert steps, "expected a reasoning trace"
    assert steps[0].phase is AgentPhase.OBSERVE
    assert steps[-1].phase is AgentPhase.PLAN
    assert [s.step for s in steps] == list(range(1, len(steps) + 1))
    for step in steps:
        assert "{" not in step.content and "}" not in step.content
        assert step.inputs["sensor"] == a.sensor.value


def test_build_thinking_injects_severity_note():
    a = anomaly(severity=Severity.CRITICAL)
    steps = build_thinking(a, ctx(), select_playbook("Thermal"))
    notes = [s for s in steps if "Severity is critical" in s.content]
    assert len(notes) == 1 and notes[0].phase is AgentPhase.REASON


def test_build_actions_are_proposed_and_target_the_playbook():
    a = anomaly()
    playbook = select_playbook("Thermal")
    actions = build_actions(a, ctx(), playbook)
    assert len(actions) == len(playbook.actions)
    assert all(act.status is ActionStatus.PROPOSED for act in actions)
    assert {act.action for act in actions} == {spec.action for spec in playbook.actions}


def test_template_values_reports_sun_state_and_confidence():
    values = template_values(anomaly(), {"sun_fraction": 0.0, "payload_mode": "imaging"})
    assert values["sun_state"] == "eclipsed"
    assert values["confidence"] == "90%"
    assert values["payload_mode"] == "imaging"


def test_build_report_has_sections_and_related_anomalies():
    a = anomaly()
    playbook = select_playbook("Thermal")
    state = IncidentState("inc-1", "Thermal", playbook, a.severity.value)
    state.note_anomaly(a)
    state.actions.extend(build_actions(a, ctx(), playbook))
    report = build_report(state)
    assert isinstance(report, ReportReadyMessage)
    assert report.severity is Severity.WARNING
    assert a.anomaly_id in report.related_anomaly_ids
    assert {s.heading for s in report.sections} == {
        "Timeline",
        "Root cause",
        "Actions taken",
        "Outcome",
    }


def test_note_anomaly_flags_escalation():
    a = anomaly(severity=Severity.WARNING)
    state = IncidentState("inc-1", "Thermal", select_playbook("Thermal"), a.severity.value)
    assert state.note_anomaly(a) is False
    assert state.note_anomaly(anomaly(severity=Severity.CRITICAL, anomaly_id="a2")) is True
    assert state.severity == "critical"


# -- detection integration ---------------------------------------------------- #
def test_recovery_notices_only_for_non_nominal_subsystems():
    svc = DetectionService()
    assert svc.recovery_notices() == []
    svc.incidents.status[Subsystem.THERMAL] = SystemStatus.WARNING
    notices = svc.recovery_notices()
    assert len(notices) == 1
    assert notices[0].status == "Thermal_NOMINAL"
    assert notices[0].source is StatusSource.DETECTION


# -- service ------------------------------------------------------------------ #
async def _run(payloads_by_step, context):
    """Drive an AgentService through successive observe() batches at zero delay."""
    published: list = []

    async def publish(payload) -> None:
        published.append(payload)

    agent = AgentService(step_delay_s=0, action_delay_s=0, report_delay_s=0)
    agent.set_publisher(publish)
    await agent.start()
    for batch in payloads_by_step:
        agent.observe(batch, context=context)
        await agent.idle()
    await agent.stop()
    return agent, published


def test_service_full_incident_lifecycle():
    context = reading()
    batches = [
        [anomaly()],
        [StatusUpdateMessage(
            source=StatusSource.DETECTION,
            level=Severity.INFO,
            status="Thermal_NOMINAL",
            message="recovered",
        )],
    ]
    agent, published = asyncio.run(_run(batches, context))

    thinking = [p for p in published if isinstance(p, AgentThinkingMessage)]
    actions = [p for p in published if isinstance(p, AgentActionMessage)]
    reports = [p for p in published if isinstance(p, ReportReadyMessage)]

    assert thinking and thinking[0].phase is AgentPhase.OBSERVE
    assert any(t.phase is AgentPhase.VERIFY for t in thinking)

    by_id: dict[str, list[ActionStatus]] = {}
    for act in actions:
        by_id.setdefault(act.action_id, []).append(act.status)
    assert by_id, "expected action lifecycle frames"
    for statuses in by_id.values():
        assert statuses == [ActionStatus.PROPOSED, ActionStatus.EXECUTING, ActionStatus.SUCCEEDED]

    assert len(reports) == 1
    assert "Thermal" in reports[0].title
    assert agent.incidents_handled == 1
    assert agent.actions_taken == len(by_id)
    assert agent.reports_generated == 1
    assert agent.active_incidents == 0


def test_service_escalation_emits_extra_reasoning():
    context = reading()
    batches = [
        [anomaly(severity=Severity.WARNING)],
        [anomaly(severity=Severity.CRITICAL, anomaly_id="a2")],
    ]
    agent, published = asyncio.run(_run(batches, context))
    thinking = [p for p in published if isinstance(p, AgentThinkingMessage)]
    escalations = [t for t in thinking if t.content.startswith("Escalation")]
    assert len(escalations) == 1
    assert agent.incidents_handled == 1  # same incident, not a new one


def test_service_disabled_ignores_payloads():
    async def scenario():
        published = []

        async def publish(payload):
            published.append(payload)

        agent = AgentService(enabled=False, step_delay_s=0, action_delay_s=0, report_delay_s=0)
        agent.set_publisher(publish)
        await agent.start()
        agent.observe([anomaly()], context=reading())
        await agent.idle()
        await agent.stop()
        return published

    assert asyncio.run(scenario()) == []


def test_service_reset_clears_state():
    async def scenario():
        agent = AgentService(step_delay_s=0, action_delay_s=0, report_delay_s=0)
        agent.set_publisher(lambda payload: None)
        await agent.start()
        agent.observe([anomaly()], context=reading())
        await agent.idle()
        assert agent.active_incidents == 1
        agent.reset()
        assert agent.active_incidents == 0
        assert agent.incidents_handled == 0
        await agent.stop()

    asyncio.run(scenario())


# -- API integration ---------------------------------------------------------- #
def test_health_reports_agent_counters(client):
    body = client.get("/health").json()
    assert body["agent_enabled"] is True
    assert body["agent_incidents_handled"] == 0
    assert body["agent_active_incidents"] == 0
    assert body["agent_reports_generated"] == 0
