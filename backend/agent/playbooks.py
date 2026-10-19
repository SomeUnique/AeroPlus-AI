"""Mission playbooks: the Decide/Act policy the agent follows per subsystem.

Each playbook is a small, auditable procedure: a diagnosis, an ordered reasoning
trace (observe -> reason -> plan), and a set of corrective actions. Content uses
``str.format`` placeholders (``{sensor}``, ``{value}`` ...) filled from the
anomaly and the current telemetry context, so the trace reads naturally while
staying fully deterministic and testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from models.schemas import AgentPhase


@dataclass(frozen=True)
class ActionSpec:
    action: str
    target: str
    rationale: str
    params: Dict[str, object] = None  # type: ignore[assignment]


@dataclass(frozen=True)
class Playbook:
    subsystem: str
    title: str
    diagnosis: str
    severity_note: str
    steps: List[Tuple[AgentPhase, str]]
    actions: List[ActionSpec]
    outcome: str


_THERMAL = Playbook(
    subsystem="Thermal",
    title="Battery thermal excursion",
    diagnosis="Battery cell temperature is climbing toward the safe upper limit.",
    severity_note=(
        "Severity is {severity}: sustained overheating degrades cells and erodes "
        "thermal-runaway margin, so this is treated as time-critical."
    ),
    steps=[
        (AgentPhase.OBSERVE,
         "Thermal anomaly: {sensor}={value} (expected {lo}..{hi}) from the "
         "{detector} detector, severity {severity}, confidence {confidence}."),
        (AgentPhase.REASON,
         "Pack temperature is rising under load; left unchecked it risks "
         "accelerated ageing and loss of thermal margin."),
        (AgentPhase.REASON,
         "Correlating context: {payload_mode} payload with a {sun_state} array "
         "suggests load-driven heating rather than a sensor fault."),
        (AgentPhase.PLAN,
         "Shed non-critical payload load and reduce charge rate to cut ohmic "
         "heating while preserving bus power."),
    ],
    actions=[
        ActionSpec("shed_payload_power", "Payload",
                   "Remove ~44 W of imaging load to reduce dissipation.", {"mode": "idle"}),
        ActionSpec("reduce_charge_rate", "Power",
                   "Lower charging current to reduce battery heating.", {"target_c_rate": 0.2}),
    ],
    outcome="Thermal excursion cleared; battery temperature back inside limits.",
)

_POWER = Playbook(
    subsystem="Power",
    title="Solar array degradation",
    diagnosis="Array output is below the illumination-adjusted model in sunlight.",
    severity_note=(
        "Severity is {severity}: reduced generation threatens the orbit-average "
        "power balance and battery state of charge."
    ),
    steps=[
        (AgentPhase.OBSERVE,
         "Power anomaly: {sensor}={value} (expected {lo}..{hi}) from the "
         "{detector} detector, severity {severity}, confidence {confidence}."),
        (AgentPhase.REASON,
         "The residual (measured minus modelled) is negative while {sun_state}, "
         "so generation - not illumination - is degraded."),
        (AgentPhase.REASON,
         "Net power balance is negative, so the battery will carry the deficit "
         "through the next eclipse unless load is trimmed."),
        (AgentPhase.PLAN,
         "Enter a low-power profile and defer non-critical loads to protect the "
         "battery while the fault is diagnosed."),
    ],
    actions=[
        ActionSpec("shed_payload_power", "Payload",
                   "Drop imaging load to reduce demand immediately.", {"mode": "idle"}),
        ActionSpec("enter_low_power_mode", "Power",
                   "Cap bus discharge and prioritise essential loads.",
                   {"bus_priority": "essential"}),
    ],
    outcome="Generation deficit managed; bus within safe state-of-charge.",
)

_COMMS = Playbook(
    subsystem="Comms",
    title="Downlink signal loss",
    diagnosis="Downlink RSSI has collapsed; telemetry frames are being dropped.",
    severity_note=(
        "Severity is {severity}: a sustained downlink loss blinds the ground "
        "segment and delays commanding."
    ),
    steps=[
        (AgentPhase.OBSERVE,
         "Comms anomaly: {sensor}={value} dBm (expected {lo}..{hi}) from the "
         "{detector} detector, severity {severity}, confidence {confidence}."),
        (AgentPhase.REASON,
         "RSSI is well below the recent baseline, indicating an antenna, "
         "pointing, or link-budget problem rather than nominal fading."),
        (AgentPhase.REASON,
         "Packet loss rises with the fade, so ground situational awareness is "
         "degrading in real time."),
        (AgentPhase.PLAN,
         "Buffer telemetry onboard and request a retransmit window on the next "
         "acquisition so no science data is lost."),
    ],
    actions=[
        ActionSpec("enable_telemetry_buffering", "Comms",
                   "Persist frames onboard until the link recovers.", {"capacity_min": 30}),
        ActionSpec("request_ground_retransmit", "Comms",
                   "Ask the ground station to re-acquire the link next pass.",
                   {"retry": 3}),
    ],
    outcome="Downlink re-established; buffered telemetry delivered to ground.",
)

_ADCS = Playbook(
    subsystem="ADCS",
    title="Attitude rate excursion",
    diagnosis="Body rate is above the nominal attitude-control envelope.",
    severity_note=(
        "Severity is {severity}: excessive rates risk pointing loss and pointing-"
        "dependent payload and comms performance."
    ),
    steps=[
        (AgentPhase.OBSERVE,
         "ADCS anomaly: {sensor}={value} deg/s (expected {lo}..{hi}) from the "
         "{detector} detector, severity {severity}, confidence {confidence}."),
        (AgentPhase.REASON,
         "Body rate exceeds the slew envelope, so the controller is not damping "
         "as expected."),
        (AgentPhase.PLAN,
         "Command a rate-damping hold to return the body within the attitude "
         "deadband."),
    ],
    actions=[
        ActionSpec("hold_attitude", "ADCS",
                   "Freeze the current attitude and damp residual rates.",
                   {"mode": "inertial"}),
    ],
    outcome="Body rates damped; attitude control authority restored.",
)

_PAYLOAD = Playbook(
    subsystem="Payload",
    title="Payload subsystem anomaly",
    diagnosis="Payload behaviour is outside its nominal envelope.",
    severity_note="Severity is {severity}.",
    steps=[
        (AgentPhase.OBSERVE,
         "Payload anomaly: {sensor}={value} (expected {lo}..{hi}) from the "
         "{detector} detector, severity {severity}."),
        (AgentPhase.REASON,
         "Payload draw is inconsistent with the nominal duty cycle, so the "
         "instrument may be faulted."),
        (AgentPhase.PLAN, "Power down the payload until it is re-qualified."),
    ],
    actions=[
        ActionSpec("power_down_payload", "Payload",
                   "Remove payload load to protect the bus.", {}),
    ],
    outcome="Payload isolated; bus load nominal.",
)

_UNKNOWN = Playbook(
    subsystem="Unknown",
    title="Unclassified anomaly",
    diagnosis="An anomaly was raised on a subsystem without a specific playbook.",
    severity_note="Severity is {severity}.",
    steps=[
        (AgentPhase.OBSERVE, "Anomaly on {sensor}={value}, severity {severity}."),
        (AgentPhase.REASON,
         "No dedicated playbook matches this subsystem; applying the safe default."),
        (AgentPhase.PLAN, "Preserve the bus: shed non-critical load and observe."),
    ],
    actions=[
        ActionSpec("shed_payload_power", "Payload",
                   "Default safe action: reduce non-critical load.", {"mode": "idle"}),
    ],
    outcome="Default safe response applied.",
)

PLAYBOOKS: Dict[str, Playbook] = {
    "Thermal": _THERMAL,
    "Power": _POWER,
    "Comms": _COMMS,
    "ADCS": _ADCS,
    "Payload": _PAYLOAD,
}


def select_playbook(subsystem: str | None) -> Playbook:
    return PLAYBOOKS.get(subsystem or "", _UNKNOWN)
