"""Communications service: the SIMULATED space -> ground data path.

Wires the onboard packetizer, the store-and-forward buffer, the RF link model,
the ground-station ingest service and the uplink command queue into one
transport-agnostic orchestrator:

    reading -> packets -> onboard buffer -> (pass) -> RF link -> ground ingest
        -> decoded telemetry (returned to the caller for the backend pipeline)

The service is deliberately free of FastAPI/asyncio: :meth:`tick` advances the
simulation by a number of mission seconds and *returns* the WS payloads to emit
and the telemetry packets the ground just received. ``app/main.py`` wraps them in
envelopes and pushes them through the hub, which keeps this whole layer unit
testable without a server.

Everything is SIMULATED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

from comms.commands import CommandError, UplinkCommandQueue
from comms.ingest import GroundStationIngest
from comms.link import LinkConfig, RfLinkModel, TxOutcome
from comms.orbit import (
    GroundStation,
    PassPredictor,
    PassWindow,
    align_first_pass,
)
from comms.packets import OnboardBuffer, Packetizer, TelemetryPacket
from models.schemas import (
    CommandStatusMessage,
    LinkState,
    LinkStatusMessage,
    PassEvent,
    PassInfoMessage,
    PassWindowModel,
    Severity,
    StatusSource,
    StatusUpdateMessage,
    TelemetryReading,
)
from services.clock import MissionClock
from services.faults import FaultManager

CommsPayload = Union[LinkStatusMessage, PassInfoMessage, CommandStatusMessage, StatusUpdateMessage]

#: Largest simulation step inside the comms layer, so a fast demo (high time
#: scale) cannot tunnel through a short pass.
MAX_STEP_S = 5.0


@dataclass
class CommsTickResult:
    """What one :meth:`CommsService.tick` produced."""

    payloads: List[CommsPayload] = field(default_factory=list)
    downlinked: List[TelemetryPacket] = field(default_factory=list)


class CommsService:
    def __init__(
        self,
        *,
        clock: MissionClock,
        faults: FaultManager,
        station: GroundStation,
        orbit_period_s: float,
        inclination_deg: float,
        first_pass_delay_s: float = 15.0,
        buffer_capacity: int = 8192,
        link_config: Optional[LinkConfig] = None,
        max_commands_per_pass: int = 3,
        command_ack_delay_s: float = 3.0,
        link_status_interval_s: float = 10.0,
        pass_info_interval_s: float = 30.0,
        link_seed: Optional[int] = None,
    ) -> None:
        self.clock = clock
        self.faults = faults
        self.station = station
        self.command_ack_delay_s = command_ack_delay_s
        self.link_status_interval_s = link_status_interval_s
        self.pass_info_interval_s = pass_info_interval_s

        orbit, first_pass = align_first_pass(
            station=station,
            period_s=orbit_period_s,
            inclination_deg=inclination_deg,
            first_pass_delay_s=first_pass_delay_s,
        )
        self.orbit = orbit
        self.first_pass = first_pass
        self.predictor = PassPredictor(orbit, station)

        self.buffer = OnboardBuffer(capacity=buffer_capacity)
        self.packetizer = Packetizer()
        self.link = RfLinkModel(link_config or LinkConfig(), seed=link_seed)
        self.ingest = GroundStationIngest(self._on_forwarded)
        self.queue = UplinkCommandQueue(max_per_pass=max_commands_per_pass)

        self._cursor = 0.0
        self._announced = False
        self._forwarded: List[TelemetryPacket] = []
        self._current_pass: Optional[PassWindow] = None
        self._next_pass: Optional[PassWindow] = None
        self._last_pass_info_s = 0.0
        self._last_link_status_s = 0.0
        self._announced_pass_id: Optional[str] = None
        self._pending_acks: Dict[str, float] = {}

    # -- spacecraft side ------------------------------------------------------ #
    def packetize(self, reading: TelemetryReading) -> List[TelemetryPacket]:
        """Build packets for one reading and enqueue them onboard (SIMULATED)."""
        packets = self.packetizer.packetize(reading, reading.timestamp.timestamp())
        for packet in packets:
            self.buffer.append(packet)
        return packets

    # -- main loop ------------------------------------------------------------ #
    def tick(self, *, now_s: float) -> CommsTickResult:
        """Advance the comms simulation to ``now_s`` mission seconds."""
        payloads: List[CommsPayload] = []
        if not self._announced:
            self._announced = True
            self._ensure_next_pass()
            self._emit_link_status(payloads, force=True)
            self._emit_next_pass(payloads, force=True)

        while self._cursor < now_s - 1e-9:
            step = min(MAX_STEP_S, now_s - self._cursor)
            self._cursor += step
            self._step(step, payloads)

        self._flush_acks(payloads)
        return CommsTickResult(payloads=payloads, downlinked=self._take_forwarded())

    def _step(self, step: float, payloads: List[CommsPayload]) -> None:
        elevation = self.predictor.elevation_at(self._cursor)
        previous = self.link.state
        state = self.link.advance(
            dt=step,
            elevation_deg=elevation,
            min_elevation_deg=self.station.min_elevation_deg,
            effects=self.faults.effects(),
        )

        if previous is LinkState.NO_CONTACT and state is not LinkState.NO_CONTACT:
            self._on_aos(payloads)
        elif previous is not LinkState.NO_CONTACT and state is LinkState.NO_CONTACT:
            self._on_los(payloads)

        if state is LinkState.CONNECTED:
            self._downlink_once(payloads)
            payloads.extend(self.queue.promote_approved())
            self._uplink_commands(payloads)

        self._emit_link_status(payloads)
        self._emit_next_pass(payloads)

    # -- downlink ------------------------------------------------------------- #
    def _downlink_once(self, payloads: List[CommsPayload]) -> None:
        while True:
            packet = self.buffer.peek_packet()
            if packet is None:
                break
            raw = packet.encode()
            if not self.link.has_budget(len(raw)):
                break
            self.buffer.pop_packet()
            self.link.spend(len(raw))
            outcome, arrived = self.link.transmit(raw)
            if outcome is TxOutcome.LOST:
                continue
            self.ingest.receive(arrived)
            if outcome is TxOutcome.DELIVERED:
                self.link.last_contact_time = self.clock.datetime_at(self._cursor)

    def _uplink_commands(self, payloads: List[CommsPayload]) -> None:
        while True:
            command = self.queue.next_for_uplink()
            if command is None:
                break
            payloads.append(command)
            self._pending_acks[command.command_id] = self._cursor + self.command_ack_delay_s

    def _flush_acks(self, payloads: List[CommsPayload]) -> None:
        due = [cid for cid, at in self._pending_acks.items() if self._cursor >= at]
        for command_id in due:
            del self._pending_acks[command_id]
            try:
                payloads.append(self.queue.mark_executed(command_id))
            except CommandError:
                continue

    def _on_forwarded(self, packet: TelemetryPacket) -> None:
        self._forwarded.append(packet)

    def _take_forwarded(self) -> List[TelemetryPacket]:
        forwarded, self._forwarded = self._forwarded, []
        return forwarded

    # -- pass lifecycle ------------------------------------------------------- #
    def _ensure_next_pass(self) -> None:
        if self._next_pass is None or self._next_pass.los_s < self._cursor:
            self._next_pass = self.predictor.find_pass_after(self._cursor)

    def _on_aos(self, payloads: List[CommsPayload]) -> None:
        self._ensure_next_pass()
        self._current_pass = self._next_pass
        payloads.append(
            self._pass_payload(PassEvent.AOS, self._current_pass, seconds_to_aos=0.0)
        )
        payloads.extend(self.queue.on_pass_start())

    def _on_los(self, payloads: List[CommsPayload]) -> None:
        ended = self._current_pass
        self._current_pass = None
        self.queue.on_pass_end()
        self._next_pass = None
        self._ensure_next_pass()
        payloads.append(
            self._pass_payload(
                PassEvent.LOS,
                ended,
                seconds_to_aos=self._countdown(self._next_pass),
            )
        )
        self._announced_pass_id = None

    def _countdown(self, window: Optional[PassWindow]) -> Optional[float]:
        if window is None:
            return None
        return max(0.0, window.aos_s - self._cursor)

    # -- payload builders ----------------------------------------------------- #
    def _emit_link_status(self, payloads: List[CommsPayload], *, force: bool = False) -> None:
        due = self._cursor - self._last_link_status_s >= self.link_status_interval_s
        if not force and not due:
            return
        self._last_link_status_s = self._cursor
        payloads.append(self.link_status())

    def link_status(self) -> LinkStatusMessage:
        aos = self.clock.datetime_at(self._current_pass.aos_s) if self._current_pass else None
        los_expected = self.clock.datetime_at(self._current_pass.los_s) if self._current_pass else None
        return LinkStatusMessage(
            link_state=self.link.state,
            signal_quality=round(self.link.signal_quality, 4),
            packets_received=self.link.stats.packets_received,
            packets_lost=self.link.stats.packets_lost,
            crc_failures=self.link.stats.crc_failures,
            buffer_depth=self.buffer.depth,
            last_contact_time=self.link.last_contact_time,
            aos=aos,
            los_expected=los_expected,
        )

    def _emit_next_pass(self, payloads: List[CommsPayload], *, force: bool = False) -> None:
        if self.link.state is not LinkState.NO_CONTACT:
            return
        self._ensure_next_pass()
        window = self._next_pass
        if window is None:
            return
        due = self._cursor - self._last_pass_info_s >= self.pass_info_interval_s
        changed = self._announced_pass_id != window.pass_id
        if not force and not due and not changed:
            return
        self._last_pass_info_s = self._cursor
        self._announced_pass_id = window.pass_id
        payloads.append(
            self._pass_payload(PassEvent.NEXT_PASS, window, seconds_to_aos=self._countdown(window))
        )

    def _pass_payload(
        self,
        event: PassEvent,
        window: Optional[PassWindow],
        *,
        seconds_to_aos: Optional[float],
    ) -> PassInfoMessage:
        model = None
        if window is not None:
            model = PassWindowModel(
                pass_id=window.pass_id,
                aos=self.clock.datetime_at(window.aos_s),
                los=self.clock.datetime_at(window.los_s),
                duration_s=round(window.duration_s(), 3),
                max_elevation_deg=round(window.max_elevation_deg, 2),
            )
        return PassInfoMessage(
            event=event,
            station=self.station.name,
            time_scale=self.clock.time_scale,
            window=model,
            seconds_to_aos=round(seconds_to_aos, 2) if seconds_to_aos is not None else None,
        )

    # -- operator command API (called by the WS layer) ------------------------ #
    def submit_command(self, command: str, params: Optional[dict] = None, *, operator: str = "operator") -> CommandStatusMessage:
        return self.queue.submit(command, params, operator=operator)

    def approve_command(self, command_id: str, *, operator: str = "operator") -> CommandStatusMessage:
        return self.queue.approve(command_id, operator=operator)

    def reject_command(self, command_id: str) -> CommandStatusMessage:
        return self.queue.reject(command_id)

    # -- introspection -------------------------------------------------------- #
    def next_pass_summary(self) -> Optional[PassWindow]:
        self._ensure_next_pass()
        return self._next_pass

    def pass_info_snapshot(self) -> Optional[PassInfoMessage]:
        """Current next-pass snapshot, sent to a client the moment it connects."""
        window = self.next_pass_summary()
        if window is None:
            return None
        return self._pass_payload(
            PassEvent.NEXT_PASS, window, seconds_to_aos=self._countdown(window)
        )

    def seconds_to_next_aos(self) -> Optional[float]:
        return self._countdown(self.next_pass_summary())

    def status_update(self, status: str, message: str, level: Severity = Severity.INFO) -> StatusUpdateMessage:
        return StatusUpdateMessage(
            source=StatusSource.OPERATOR, level=level, status=status, message=message
        )
