"""Pydantic contracts shared by the WebSocket stream, REST API and (later)
the detection/agent subsystems and the Next.js frontend.

Every WebSocket frame is an *envelope*:

    {"type": "...", "seq": 123, "ts": "...", "payload": {...}}

The `WSMessage` discriminated union is the single source of truth for the
wire format: serialise with `model_dump(mode="json")`, validate incoming or
replayed frames with `WSMessage.model_validate_json(...)`.

Adding a new message type:
    1. add the payload model below
    2. add a `Literal["<name>"]` envelope variant
    3. register it in `WSMessageUnion` / `WS_MESSAGE_MODELS`
    4. (optional) add a `make_<name>()` helper
Nothing else should need to change on either side of the socket.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class MessageType(str, Enum):
    TELEMETRY = "telemetry"
    LINK_STATUS = "link_status"
    PASS_INFO = "pass_info"
    ANOMALY = "anomaly"
    AGENT_THINKING = "agent_thinking"
    AGENT_ACTION = "agent_action"
    COMMAND_STATUS = "command_status"
    STATUS_UPDATE = "status_update"
    REPORT_READY = "report_ready"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Sensor(str, Enum):
    BATTERY_VOLTAGE = "battery_voltage"
    BATTERY_TEMP = "battery_temp"
    SOLAR_CURRENT = "solar_current"
    BUS_TEMP = "bus_temp"
    SIGNAL_STRENGTH = "signal_strength"
    GYRO_RATE = "gyro_rate"
    PAYLOAD_POWER = "payload_power"


class FaultType(str, Enum):
    BATTERY_OVERHEAT = "battery_overheat"
    SOLAR_DEGRADATION = "solar_degradation"
    SIGNAL_LOSS = "signal_loss"


class AnomalyKind(str, Enum):
    OUT_OF_RANGE = "out_of_range"
    RATE_OF_CHANGE = "rate_of_change"
    DRIFT = "drift"
    SIGNAL_DEGRADATION = "signal_degradation"
    CORRELATED = "correlated"
    ML_ANOMALY = "ml_anomaly"


class DetectorKind(str, Enum):
    """Which detector produced an anomaly (mirrors detection.config.DetectorType)."""

    RULE = "rule"
    ZSCORE = "zscore"
    RATE = "rate"
    CORRELATED = "correlated"
    IFOREST = "iforest"


class AgentPhase(str, Enum):
    OBSERVE = "observe"
    REASON = "reason"
    PLAN = "plan"
    VERIFY = "verify"


class ActionStatus(str, Enum):
    PROPOSED = "proposed"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PayloadMode(str, Enum):
    IDLE = "idle"
    IMAGING = "imaging"
    DOWNLINK = "downlink"


class StatusSource(str, Enum):
    SYSTEM = "system"
    DETECTION = "detection"
    AGENT = "agent"
    OPERATOR = "operator"


class LinkState(str, Enum):
    """SIMULATED RF link state between the spacecraft and the ground station."""

    NO_CONTACT = "NO_CONTACT"
    ACQUIRING = "ACQUIRING"
    CONNECTED = "CONNECTED"


class PassEvent(str, Enum):
    """Lifecycle of a ground-station contact window."""

    NEXT_PASS = "next_pass"
    AOS = "aos"
    LOS = "los"


class CommandStatus(str, Enum):
    """Uplink command lifecycle. Ground commands never skip operator approval."""

    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    QUEUED_FOR_PASS = "QUEUED_FOR_PASS"
    UPLINKED = "UPLINKED"
    EXECUTED = "EXECUTED"


class CommandAction(str, Enum):
    """Operator action on a queued command (client -> server)."""

    SUBMIT = "submit"
    APPROVE = "approve"
    REJECT = "reject"


# --------------------------------------------------------------------------- #
# Payload models
# --------------------------------------------------------------------------- #
class TelemetryReading(BaseModel):
    """One synthetic telemetry frame (1 Hz nominal).

    Units are fixed here and must not change: they are part of the contract.
    """

    model_config = ConfigDict(json_schema_extra={"example": {
        "timestamp": "2026-10-15T12:00:01Z", "battery_voltage": 28.14,
        "battery_temp": 24.7, "solar_current": 6.12, "bus_temp": 29.4,
        "signal_strength": -74.3, "gyro_rate": 0.004, "payload_power": 45.2,
        "in_eclipse": False, "sun_fraction": 0.98, "payload_mode": "imaging"}})

    timestamp: datetime
    battery_voltage: float = Field(description="Bus battery voltage [V]")
    battery_temp: float = Field(description="Battery pack temperature [degC]")
    solar_current: float = Field(description="Solar array output current [A]")
    bus_temp: float = Field(description="Avionics bus temperature [degC]")
    signal_strength: float = Field(description="Downlink RSSI [dBm]")
    gyro_rate: float = Field(description="Body rate magnitude [deg/s]")
    payload_power: float = Field(description="Payload draw [W]")
    in_eclipse: bool = Field(description="True while the spacecraft is in Earth's shadow")
    sun_fraction: float = Field(
        ge=0.0,
        le=1.0,
        description="Modeled solar illumination 0..1 (ephemeris context, includes penumbra)",
    )
    payload_mode: PayloadMode = Field(description="Payload operating mode")


class AnomalyMessage(BaseModel):
    anomaly_id: str = Field(default_factory=lambda: new_id("anom"))
    sensor: Sensor
    kind: AnomalyKind
    severity: Severity
    value: float = Field(description="Observed value at detection time")
    expected_min: Optional[float] = None
    expected_max: Optional[float] = None
    baseline: Optional[float] = Field(
        default=None, description="Model/rolling baseline the value deviates from"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    detected_at: datetime = Field(default_factory=utcnow)
    description: str
    # --- detection metadata (populated by the detection layer) ---
    detector_type: Optional[DetectorKind] = Field(
        default=None,
        description="Detector that raised this: rule | zscore | rate | correlated | iforest",
    )
    subsystem: Optional[str] = Field(
        default=None, description="Owning subsystem: Power | Thermal | Comms | Payload | ADCS"
    )
    incident_id: Optional[str] = Field(
        default=None, description="Incident this anomaly was grouped into"
    )
    anomaly_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="ML detector confidence score 0..1 (higher = more anomalous); null for rule/statistical",
    )
    contributing_sensors: List[str] = Field(
        default_factory=list,
        description="Sensors that contributed most to an ML anomaly (explainability)",
    )


class AgentThinkingMessage(BaseModel):
    """A single reasoning step emitted by the co-pilot (chain-of-thought trace)."""

    step: int = Field(ge=1)
    phase: AgentPhase
    content: str = Field(description="Human-readable reasoning for this step")
    inputs: Dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: Optional[int] = Field(default=None, ge=0)


class AgentActionMessage(BaseModel):
    action_id: str = Field(default_factory=lambda: new_id("act"))
    action: str = Field(description="Machine name, e.g. 'shed_payload_power'")
    target: Optional[str] = Field(default=None, description="Subsystem affected")
    params: Dict[str, Any] = Field(default_factory=dict)
    rationale: str
    status: ActionStatus = ActionStatus.PROPOSED
    created_at: datetime = Field(default_factory=utcnow)


class StatusUpdateMessage(BaseModel):
    source: StatusSource = StatusSource.SYSTEM
    level: Severity = Severity.INFO
    status: str = Field(description="Short machine code, e.g. 'nominal'")
    message: str


class ReportSection(BaseModel):
    heading: str
    body: str


class ReportReadyMessage(BaseModel):
    report_id: str = Field(default_factory=lambda: new_id("rpt"))
    title: str
    summary: str
    severity: Severity
    sections: List[ReportSection] = Field(default_factory=list)
    related_anomaly_ids: List[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- #
# Communications payloads (SIMULATED RF link + ground station)
# --------------------------------------------------------------------------- #
class LinkStatusMessage(BaseModel):
    """SIMULATED RF link + onboard store-and-forward buffer state."""

    simulated: Literal[True] = Field(default=True, description="Always SIMULATED - no real hardware")
    link_state: LinkState
    signal_quality: float = Field(
        ge=0.0, le=1.0, description="0..1, derived from elevation and link faults"
    )
    packets_received: int = Field(ge=0, description="Packets that reached the ground intact")
    packets_lost: int = Field(ge=0, description="Packets lost in transit (dropped at the channel)")
    crc_failures: int = Field(ge=0, description="Packets corrupted in transit (CRC failed on the ground)")
    buffer_depth: int = Field(ge=0, description="Packets waiting onboard (store-and-forward)")
    last_contact_time: Optional[datetime] = Field(
        default=None, description="Simulated UTC of the last successfully received packet"
    )
    aos: Optional[datetime] = Field(default=None, description="Start of the current pass (in contact)")
    los_expected: Optional[datetime] = Field(default=None, description="Predicted end of the current pass")


class PassWindowModel(BaseModel):
    """One simulated ground-station contact window (AOS -> LOS)."""

    pass_id: str
    aos: datetime
    los: datetime
    duration_s: float = Field(ge=0, description="Pass length in simulated seconds")
    max_elevation_deg: float = Field(description="Peak elevation above the horizon [deg]")


class PassInfoMessage(BaseModel):
    """Simulated pass schedule for the Karachi ground station."""

    simulated: Literal[True] = Field(default=True, description="Always SIMULATED - no real ground station")
    event: PassEvent
    station: str
    time_scale: float = Field(gt=0, description="Simulated seconds advanced per wall-clock second")
    window: Optional[PassWindowModel] = Field(
        default=None,
        description="The pass this event refers to: upcoming (next_pass), starting (aos) or ended (los)",
    )
    seconds_to_aos: Optional[float] = Field(
        default=None, ge=0, description="Sim-time countdown to the next acquisition of signal"
    )


class CommandStatusMessage(BaseModel):
    """Uplink command snapshot. Every transition is re-emitted under one command_id."""

    command_id: str = Field(default_factory=lambda: new_id("cmd"))
    command: str = Field(description="Machine name, e.g. 'ENTER_SAFE_MODE'")
    params: Dict[str, Any] = Field(default_factory=dict)
    status: CommandStatus
    detail: str = Field(default="", description="Plain-language explanation of this transition")
    submitted_by: str = Field(default="operator", description="Operator who queued the command")
    approved_by: Optional[str] = Field(default=None, description="Operator who approved it (human-in-the-loop)")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class UplinkCommandRequest(BaseModel):
    """Client -> server frame: queue / approve / reject an uplink command."""

    type: Literal["command_request"] = "command_request"
    action: CommandAction
    command: Optional[str] = Field(default=None, description="Command name (required for submit)")
    command_id: Optional[str] = Field(default=None, description="Target command (required for approve/reject)")
    params: Dict[str, Any] = Field(default_factory=dict)
    operator: str = Field(default="operator", description="Operator name recorded on the command")


# --------------------------------------------------------------------------- #
# Envelopes (one per message type) + discriminated union
# --------------------------------------------------------------------------- #
class _Envelope(BaseModel):
    seq: int = Field(ge=0, description="Monotonic per-connection stream sequence")
    ts: datetime = Field(default_factory=utcnow)


class TelemetryWS(_Envelope):
    type: Literal["telemetry"] = "telemetry"
    payload: TelemetryReading


class AnomalyWS(_Envelope):
    type: Literal["anomaly"] = "anomaly"
    payload: AnomalyMessage


class AgentThinkingWS(_Envelope):
    type: Literal["agent_thinking"] = "agent_thinking"
    payload: AgentThinkingMessage


class AgentActionWS(_Envelope):
    type: Literal["agent_action"] = "agent_action"
    payload: AgentActionMessage


class StatusUpdateWS(_Envelope):
    type: Literal["status_update"] = "status_update"
    payload: StatusUpdateMessage


class ReportReadyWS(_Envelope):
    type: Literal["report_ready"] = "report_ready"
    payload: ReportReadyMessage


class LinkStatusWS(_Envelope):
    type: Literal["link_status"] = "link_status"
    payload: LinkStatusMessage


class PassInfoWS(_Envelope):
    type: Literal["pass_info"] = "pass_info"
    payload: PassInfoMessage


class CommandStatusWS(_Envelope):
    type: Literal["command_status"] = "command_status"
    payload: CommandStatusMessage


WSMessageUnion = Annotated[
    Union[
        TelemetryWS,
        AnomalyWS,
        AgentThinkingWS,
        AgentActionWS,
        StatusUpdateWS,
        ReportReadyWS,
        LinkStatusWS,
        PassInfoWS,
        CommandStatusWS,
    ],
    Field(discriminator="type"),
]

WS_MESSAGE_MODELS: Dict[str, type[_Envelope]] = {
    "telemetry": TelemetryWS,
    "anomaly": AnomalyWS,
    "agent_thinking": AgentThinkingWS,
    "agent_action": AgentActionWS,
    "status_update": StatusUpdateWS,
    "report_ready": ReportReadyWS,
    "link_status": LinkStatusWS,
    "pass_info": PassInfoWS,
    "command_status": CommandStatusWS,
}


_WS_ADAPTER = TypeAdapter(WSMessageUnion)
_CLIENT_ADAPTER = TypeAdapter(UplinkCommandRequest)


def dumps(message: _Envelope) -> Dict[str, Any]:
    """Envelope -> JSON-ready dict for WebSocket.send_json()."""
    return message.model_dump(mode="json")


def loads(data: Dict[str, Any] | bytes | str) -> WSMessageUnion:
    """JSON -> validated, discriminated envelope.

    Raises pydantic.ValidationError on unknown types or malformed payloads -
    used for incoming frames and in tests to pin the wire contract.
    """
    return _WS_ADAPTER.validate_python(data)


def load_client_frame(data: Dict[str, Any] | bytes | str) -> UplinkCommandRequest:
    """Client -> server frame. Raises pydantic.ValidationError when unusable."""
    return _CLIENT_ADAPTER.validate_python(data)


# --------------------------------------------------------------------------- #
# Constructors (keep call sites short and consistent)
# --------------------------------------------------------------------------- #
def make_telemetry(reading: TelemetryReading, seq: int) -> TelemetryWS:
    return TelemetryWS(seq=seq, ts=reading.timestamp, payload=reading)


def make_anomaly(payload: AnomalyMessage, seq: int) -> AnomalyWS:
    return AnomalyWS(seq=seq, payload=payload)


def make_agent_thinking(payload: AgentThinkingMessage, seq: int) -> AgentThinkingWS:
    return AgentThinkingWS(seq=seq, payload=payload)


def make_agent_action(payload: AgentActionMessage, seq: int) -> AgentActionWS:
    return AgentActionWS(seq=seq, payload=payload)


def make_status_update(payload: StatusUpdateMessage, seq: int) -> StatusUpdateWS:
    return StatusUpdateWS(seq=seq, payload=payload)


def make_report_ready(payload: ReportReadyMessage, seq: int) -> ReportReadyWS:
    return ReportReadyWS(seq=seq, payload=payload)


def make_link_status(payload: LinkStatusMessage, seq: int) -> LinkStatusWS:
    return LinkStatusWS(seq=seq, payload=payload)


def make_pass_info(payload: PassInfoMessage, seq: int) -> PassInfoWS:
    return PassInfoWS(seq=seq, payload=payload)


def make_command_status(payload: CommandStatusMessage, seq: int) -> CommandStatusWS:
    return CommandStatusWS(seq=seq, payload=payload)


# --------------------------------------------------------------------------- #
# REST contracts
# --------------------------------------------------------------------------- #
class InjectFaultRequest(BaseModel):
    type: FaultType


class FaultState(BaseModel):
    type: FaultType
    progress: float = Field(ge=0.0, le=1.0, description="0 = just armed, 1 = fully developed")
    active: bool = True


class InjectFaultResponse(BaseModel):
    message: str
    active_faults: List[FaultState] = []


class TimeScaleRequest(BaseModel):
    """Demo time acceleration: simulated seconds advanced per wall-clock second."""

    time_scale: float = Field(ge=1.0, le=600.0, description="1 = real time, 600 = fastest demo")


class TimeScaleResponse(BaseModel):
    message: str
    previous_time_scale: float
    time_scale: float


class ResetResponse(BaseModel):
    status: Literal["reset"] = "reset"
    message: str
    readings_regenerated: int = 0


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    service: str
    version: str
    uptime_s: float
    clients_connected: int
    readings_generated: int
    readings_dropped: int
    frames_delivered: int
    last_telemetry_age_s: Optional[float] = None
    active_faults: List[FaultType] = []
    subsystems: Dict[str, str] = Field(
        default_factory=dict, description="Subsystem health: Power/Thermal/Comms/Payload/ADCS -> NOMINAL|WARNING|CRITICAL"
    )
    anomalies_raised: int = 0
    telemetry_hz: float
    agent_enabled: bool = True
    agent_incidents_handled: int = 0
    agent_active_incidents: int = 0
    agent_actions_taken: int = 0
    agent_reports_generated: int = 0
    agent_thinking_steps: int = 0
    # --- SIMULATED communications (orbit / RF link / ground station) ---
    time_scale: float = Field(default=1.0, description="Simulated seconds per wall-clock second")
    ground_station: str = Field(default="", description="SIMULATED ground station name")
    link_state: LinkState = LinkState.NO_CONTACT
    signal_quality: float = Field(default=0.0, ge=0.0, le=1.0)
    buffer_depth: int = Field(default=0, ge=0, description="Onboard store-and-forward backlog")
    packets_received: int = 0
    packets_lost: int = 0
    crc_failures: int = 0
    packets_forwarded: int = Field(default=0, description="Packets verified + forwarded by ground ingest")
    last_contact_time: Optional[datetime] = None
    next_pass_aos: Optional[datetime] = None
    seconds_to_next_aos: Optional[float] = None
    next_pass_max_elevation_deg: Optional[float] = None
    downlink_bps: float = 0.0
    command_queue: Dict[str, int] = Field(
        default_factory=dict, description="Uplink commands per CommandStatus (zero-filled)"
    )


class ErrorResponse(BaseModel):
    detail: str
