"""Wire-contract tests: the WS envelope must not drift silently."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from models.schemas import (
    FaultType,
    MessageType,
    Sensor,
    Severity,
    WS_MESSAGE_MODELS,
    AgentActionMessage,
    AgentPhase,
    AgentThinkingMessage,
    AnomalyKind,
    AnomalyMessage,
    CommandStatus,
    CommandStatusMessage,
    LinkState,
    LinkStatusMessage,
    PassEvent,
    PassInfoMessage,
    PassWindowModel,
    ReportReadyMessage,
    StatusSource,
    StatusUpdateMessage,
    TelemetryReading,
    dumps,
    loads,
    make_agent_action,
    make_agent_thinking,
    make_anomaly,
    make_command_status,
    make_link_status,
    make_pass_info,
    make_report_ready,
    make_status_update,
    make_telemetry,
    utcnow,
)


def sample_reading() -> TelemetryReading:
    return TelemetryReading(
        timestamp=utcnow(),
        battery_voltage=28.1,
        battery_temp=24.5,
        solar_current=6.2,
        bus_temp=29.0,
        signal_strength=-74.0,
        gyro_rate=0.004,
        payload_power=45.0,
        in_eclipse=False,
        sun_fraction=0.97,
        payload_mode="imaging",
    )


def sample_messages(seq: int = 1):
    return [
        make_telemetry(sample_reading(), seq),
        make_anomaly(
            AnomalyMessage(
                sensor=Sensor.BATTERY_TEMP,
                kind=AnomalyKind.OUT_OF_RANGE,
                severity=Severity.CRITICAL,
                value=61.2,
                expected_min=10.0,
                expected_max=45.0,
                confidence=0.94,
                description="Battery temperature above operating limit.",
            ),
            seq,
        ),
        make_agent_thinking(
            AgentThinkingMessage(
                step=1,
                phase=AgentPhase.REASON,
                content="Battery temp rising while voltage sags: pack fault likely.",
                inputs={"window_s": 120},
                elapsed_ms=18,
            ),
            seq,
        ),
        make_agent_action(
            AgentActionMessage(
                action="shed_payload_power",
                target="payload",
                params={"watts": 0},
                rationale="Reduce bus load during thermal excursion.",
            ),
            seq,
        ),
        make_status_update(
            StatusUpdateMessage(
                source=StatusSource.DETECTION,
                level=Severity.WARNING,
                status="anomaly_raised",
                message="battery_temp anomaly raised",
            ),
            seq,
        ),
        make_report_ready(
            ReportReadyMessage(
                title="Battery thermal excursion",
                summary="Overheat detected, payload shed, recovery verified.",
                severity=Severity.CRITICAL,
                sections=[{"heading": "Timeline", "body": "..."}],
                related_anomaly_ids=["anom_abc"],
            ),
            seq,
        ),
        make_link_status(
            LinkStatusMessage(
                link_state=LinkState.CONNECTED,
                signal_quality=0.92,
                packets_received=120,
                packets_lost=3,
                crc_failures=1,
                buffer_depth=7,
            ),
            seq,
        ),
        make_pass_info(
            PassInfoMessage(
                event=PassEvent.NEXT_PASS,
                station="Karachi GS (SIMULATED)",
                time_scale=60.0,
                window=PassWindowModel(
                    pass_id="pass_900",
                    aos=utcnow(),
                    los=utcnow(),
                    duration_s=520.0,
                    max_elevation_deg=82.5,
                ),
                seconds_to_aos=42.0,
            ),
            seq,
        ),
        make_command_status(
            CommandStatusMessage(
                command="ENTER_SAFE_MODE",
                params={"reason": "thermal"},
                status=CommandStatus.PENDING_APPROVAL,
                detail="Queued by operator; awaiting operator approval.",
            ),
            seq,
        ),
    ]


def test_every_declared_type_has_an_envelope_model():
    assert set(WS_MESSAGE_MODELS) == {m.value for m in MessageType}


@pytest.mark.parametrize("index", range(9))
def test_round_trip(index):
    message = sample_messages()[index]
    wire = dumps(message)
    assert wire["type"] == message.type
    assert wire["seq"] == 1
    restored = loads(wire)
    assert type(restored) is type(message)
    assert dumps(restored) == wire


def test_payload_is_typed_and_complete():
    telemetry = loads(dumps(sample_messages()[0]))
    assert isinstance(telemetry.payload, TelemetryReading)
    assert telemetry.payload.battery_voltage == pytest.approx(28.1)
    assert telemetry.payload.sun_fraction == pytest.approx(0.97)


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        loads({"type": "quantum_flux", "seq": 1, "ts": utcnow(), "payload": {}})


def test_missing_payload_rejected():
    with pytest.raises(ValidationError):
        loads({"type": "telemetry", "seq": 1, "ts": utcnow()})


def test_missing_type_rejected():
    with pytest.raises(ValidationError):
        loads({"seq": 1, "ts": utcnow(), "payload": {}})


def test_negative_seq_rejected():
    with pytest.raises(ValidationError):
        loads({"type": "status_update", "seq": -1, "ts": utcnow(), "payload": {
            "source": "system", "level": "info", "status": "connected", "message": "hi"}})


def test_confidence_bounds_enforced():
    with pytest.raises(ValidationError):
        AnomalyMessage(
            sensor=Sensor.BUS_TEMP,
            kind=AnomalyKind.DRIFT,
            severity=Severity.INFO,
            value=1.0,
            confidence=1.7,
            description="bad",
        )


def test_enum_values_are_stable():
    assert [m.value for m in MessageType] == [
        "telemetry",
        "link_status",
        "pass_info",
        "anomaly",
        "agent_thinking",
        "agent_action",
        "command_status",
        "status_update",
        "report_ready",
    ]
    assert FaultType.BATTERY_OVERHEAT.value == "battery_overheat"
    assert FaultType.SOLAR_DEGRADATION.value == "solar_degradation"
    assert FaultType.SIGNAL_LOSS.value == "signal_loss"
    assert AgentPhase.OBSERVE.value == "observe"
