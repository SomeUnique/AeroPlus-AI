"""SIMULATED communications subsystem.

A full, typed model of the space -> ground data path, transport-agnostic so it
can be unit tested without a server:

    packets.py   onboard framing (CRC-32) + store-and-forward buffer
    orbit.py     circular-orbit propagation + Karachi pass prediction
    link.py      RF link state machine, rate limit and impairment model
    ingest.py    ground-station CRC verify -> decode -> reorder
    commands.py  uplink command queue (operator-approval gated)
    service.py   CommsService - wires it all together for the telemetry loop

Nothing here is real hardware, a real radio or a real ground station.
"""

from __future__ import annotations

from comms.commands import (
    CommandError,
    InvalidTransitionError,
    UplinkCommandQueue,
    UPLINK_COMMANDS,
    UnknownCommandError,
)
from comms.ingest import GroundStationIngest, IngestOutcome
from comms.link import LinkConfig, LinkStats, RfLinkModel, TxOutcome
from comms.orbit import (
    DEFAULT_MIN_ELEVATION_DEG,
    GroundStation,
    OrbitModel,
    PassPredictor,
    PassWindow,
    align_first_pass,
    demo_geometry,
    upcoming_pass_summary,
)
from comms.packets import (
    BEACON_REFRACTORY_S,
    CrcError,
    OnboardBuffer,
    PacketError,
    Packetizer,
    PacketType,
    TelemetryPacket,
)
from comms.service import CommsService, CommsTickResult

__all__ = [
    "CommandError",
    "InvalidTransitionError",
    "UplinkCommandQueue",
    "UPLINK_COMMANDS",
    "UnknownCommandError",
    "GroundStationIngest",
    "IngestOutcome",
    "LinkConfig",
    "LinkStats",
    "RfLinkModel",
    "TxOutcome",
    "DEFAULT_MIN_ELEVATION_DEG",
    "GroundStation",
    "OrbitModel",
    "PassPredictor",
    "PassWindow",
    "align_first_pass",
    "demo_geometry",
    "upcoming_pass_summary",
    "BEACON_REFRACTORY_S",
    "CrcError",
    "OnboardBuffer",
    "PacketError",
    "Packetizer",
    "PacketType",
    "TelemetryPacket",
    "CommsService",
    "CommsTickResult",
]
