"""Unit + integration tests for the SIMULATED communications subsystem.

Covers the full space -> ground path in isolation (no server): packet framing
and CRC, orbit/pass prediction, the RF link state machine + impairment, ground
ingest reordering, the operator-gated uplink queue and the CommsService
orchestrator.
"""

from __future__ import annotations

import datetime as dt

import pytest

from comms import (
    CommandError,
    CrcError,
    GroundStation,
    GroundStationIngest,
    LinkConfig,
    OnboardBuffer,
    PacketError,
    Packetizer,
    PacketType,
    PassPredictor,
    RfLinkModel,
    TelemetryPacket,
    TxOutcome,
    UplinkCommandQueue,
    align_first_pass,
)
from models.schemas import (
    CommandStatus,
    LinkState,
    PassEvent,
    PayloadMode,
    TelemetryReading,
)
from services.clock import MissionClock
from services.faults import FaultManager
from services.telemetry import TelemetryEngine

EPOCH = dt.datetime(2026, 10, 15, 12, 0, 0, tzinfo=dt.timezone.utc)
STATION = GroundStation("Karachi GS (SIMULATED)", 24.86, 67.00)


def make_reading(**overrides) -> TelemetryReading:
    base = dict(
        timestamp=EPOCH,
        battery_voltage=28.1,
        battery_temp=24.5,
        solar_current=6.2,
        bus_temp=29.0,
        signal_strength=-74.0,
        gyro_rate=0.004,
        payload_power=45.0,
        in_eclipse=False,
        sun_fraction=0.97,
        payload_mode=PayloadMode.IMAGING,
    )
    base.update(overrides)
    return TelemetryReading(**base)


# -- packets ------------------------------------------------------------------ #
def test_packet_round_trips_through_a_frame():
    packet = TelemetryPacket(
        seq=7, packet_type=PacketType.HOUSEKEEPING, mission_s=42.0, payload={"a": 1}
    )
    decoded = TelemetryPacket.decode(packet.encode())
    assert decoded == packet


def test_corrupted_frame_fails_crc_not_silently():
    packet = TelemetryPacket(
        seq=1, packet_type=PacketType.HOUSEKEEPING, mission_s=1.0, payload={"a": 1}
    )
    raw = bytearray(packet.encode())
    raw[-6] ^= 0xFF  # flip a payload byte
    with pytest.raises(CrcError):
        TelemetryPacket.decode(bytes(raw))


def test_truncated_frame_is_malformed():
    with pytest.raises(PacketError):
        TelemetryPacket.decode(b"AP1")


def test_packetizer_uses_separate_sequence_counters():
    packetizer = Packetizer()
    packets = packetizer.packetize(make_reading(), 1.0)
    assert len(packets) == 1
    assert packets[0].packet_type is PacketType.HOUSEKEEPING
    assert packets[0].seq == 0
    assert packetizer.packetize(make_reading(), 2.0)[0].seq == 1


def test_packetizer_raises_beacon_on_limit_breach():
    packetizer = Packetizer()
    packetizer.packetize(make_reading(), 1.0)  # nominal, no beacon
    hot = packetizer.packetize(make_reading(battery_temp=61.0), 2.0)
    beacons = [p for p in hot if p.packet_type is PacketType.BEACON]
    assert len(beacons) == 1
    assert beacons[0].payload["sensor"] == "battery_temp"
    assert beacons[0].payload["severity"] == "critical"


def test_beacon_refractory_limits_flood():
    packetizer = Packetizer()
    packetizer.packetize(make_reading(battery_temp=61.0), 1.0)
    again = packetizer.packetize(make_reading(battery_temp=62.0), 5.0)
    assert all(p.packet_type is not PacketType.BEACON for p in again)


def test_onboard_buffer_drops_oldest_housekeeping_on_overflow():
    buffer = OnboardBuffer(capacity=3)
    for seq in range(5):
        buffer.append(
            TelemetryPacket(seq, PacketType.HOUSEKEEPING, float(seq), {"s": seq})
        )
    assert buffer.depth == 3
    assert buffer.overflow_dropped == 2
    assert buffer.pop_packet().seq == 2  # oldest two evicted


def test_onboard_buffer_prioritises_beacons():
    buffer = OnboardBuffer()
    buffer.append(TelemetryPacket(0, PacketType.HOUSEKEEPING, 0.0, {}))
    buffer.append(TelemetryPacket(0, PacketType.BEACON, 0.0, {}))
    assert buffer.peek_packet().packet_type is PacketType.BEACON
    assert buffer.pop_packet().packet_type is PacketType.BEACON
    assert buffer.pop_packet().packet_type is PacketType.HOUSEKEEPING


# -- orbit -------------------------------------------------------------------- #
def test_first_pass_is_aligned_near_the_planned_delay():
    _, first = align_first_pass(
        station=STATION, period_s=5760.0, inclination_deg=35.0, first_pass_delay_s=15.0
    )
    assert first.aos_s == pytest.approx(15.0, abs=1.0)
    assert first.duration_s() > 300.0
    assert first.max_elevation_deg > 40.0


def test_ground_station_sees_several_passes_per_simulated_day():
    orbit, _ = align_first_pass(
        station=STATION, period_s=5760.0, inclination_deg=35.0, first_pass_delay_s=15.0
    )
    predictor = PassPredictor(orbit, STATION)
    passes = predictor.passes_between(0.0, 86400.0)
    assert 4 <= len(passes) <= 8
    assert all(p.duration_s() > 60.0 for p in passes)


# -- link --------------------------------------------------------------------- #
def _link(seed: int = 1) -> RfLinkModel:
    return RfLinkModel(LinkConfig(), seed=seed)


def test_link_moves_through_acquiring_to_connected():
    link = _link()
    assert link.state is LinkState.NO_CONTACT
    link.advance(dt=1.0, elevation_deg=5.0, min_elevation_deg=10.0, effects=_no_effects())
    assert link.state is LinkState.NO_CONTACT
    link.advance(dt=1.0, elevation_deg=40.0, min_elevation_deg=10.0, effects=_no_effects())
    assert link.state is LinkState.ACQUIRING
    link.advance(dt=10.0, elevation_deg=40.0, min_elevation_deg=10.0, effects=_no_effects())
    assert link.state is LinkState.CONNECTED


def test_link_quality_falls_with_a_downlink_fault():
    from services.faults import FaultEffects

    nominal = RfLinkModel._quality(60.0, 10.0, FaultEffects())
    faulted = RfLinkModel._quality(60.0, 10.0, FaultEffects(signal_offset_dbm=-34.0))
    assert faulted < nominal


def test_rate_limit_accumulates_then_spends_budget():
    link = _link()
    raw_len = 100
    # Not connected yet: no budget accrues (ACQUIRING).
    link.advance(dt=1.0, elevation_deg=80.0, min_elevation_deg=10.0, effects=_no_effects())
    assert link.state is LinkState.ACQUIRING
    assert link.budget_bytes == 0
    for _ in range(20):
        link.advance(dt=1.0, elevation_deg=80.0, min_elevation_deg=10.0, effects=_no_effects())
    assert link.state is LinkState.CONNECTED
    before = link.budget_bytes
    assert before > raw_len
    link.spend(raw_len)
    assert link.budget_bytes == before - raw_len
    assert link.has_budget(raw_len)
    link.spend(before - raw_len)
    assert not link.has_budget(1)


def test_channel_eventually_loses_and_corrupts_packets():
    link = _link(seed=3)
    outcomes = {link.transmit(b"y" * 50)[0] for _ in range(400)}
    assert TxOutcome.LOST in outcomes
    assert TxOutcome.CORRUPTED in outcomes
    assert TxOutcome.DELIVERED in outcomes


def _no_effects():
    from services.faults import FaultEffects

    return FaultEffects()


# -- ingest ------------------------------------------------------------------- #
def test_ingest_forwards_in_sequence_order_after_a_gap():
    forwarded: list[TelemetryPacket] = []
    ingest = GroundStationIngest(forwarded.append, reorder_window=8)
    for seq in (0, 1, 3, 2):
        ingest.receive(_hk(seq, float(seq)))
    assert [p.seq for p in forwarded] == [0, 1, 2, 3]


def test_ingest_drops_duplicates():
    forwarded: list[TelemetryPacket] = []
    ingest = GroundStationIngest(forwarded.append)
    ingest.receive(_hk(0, 0.0))
    ingest.receive(_hk(0, 0.0))
    assert [p.seq for p in forwarded] == [0]
    assert ingest.duplicates == 1


def test_ingest_counts_crc_failures_and_malformed():
    forwarded: list[TelemetryPacket] = []
    ingest = GroundStationIngest(forwarded.append)
    raw = bytearray(_hk(0, 0.0))
    raw[-6] ^= 0xFF
    assert ingest.receive(bytes(raw)).value == "crc_failed"
    assert ingest.receive(b"AP1").value == "malformed"
    assert ingest.forwarded == 0


def test_beacon_bypasses_the_reorder_buffer():
    forwarded: list[TelemetryPacket] = []
    ingest = GroundStationIngest(forwarded.append, reorder_window=8)
    ingest.receive(_hk(0, 0.0))
    ingest.receive(_hk(5, 5.0))  # a gap
    beacon = TelemetryPacket(0, PacketType.BEACON, 10.0, {"sensor": "bus_temp"})
    ingest.receive(beacon.encode())
    assert forwarded[-1].packet_type is PacketType.BEACON


def _hk(seq: int, mission_s: float) -> bytes:
    return TelemetryPacket(seq, PacketType.HOUSEKEEPING, mission_s, {"seq": seq}).encode()


# -- commands ----------------------------------------------------------------- #
def test_command_requires_approval_before_uplink():
    queue = UplinkCommandQueue()
    record = queue.submit("ENTER_SAFE_MODE")
    assert record.status is CommandStatus.PENDING_APPROVAL
    queue.on_pass_start()
    assert queue.next_for_uplink() is None  # not approved yet
    queue.approve(record.command_id, operator="lead")
    queue.on_pass_start()
    uplinked = queue.next_for_uplink()
    assert uplinked is not None and uplinked.status is CommandStatus.UPLINKED
    executed = queue.mark_executed(record.command_id)
    assert executed.status is CommandStatus.EXECUTED


def test_unknown_command_is_rejected():
    queue = UplinkCommandQueue()
    with pytest.raises(CommandError):
        queue.submit("SELF_DESTRUCT")


def test_reject_removes_pending_command():
    queue = UplinkCommandQueue()
    record = queue.submit("THROTTLE_PAYLOAD")
    queue.reject(record.command_id)
    assert queue.get(record.command_id) is None


def test_commands_per_pass_are_capped():
    queue = UplinkCommandQueue(max_per_pass=1)
    for _ in range(3):
        rec = queue.submit("THROTTLE_PAYLOAD")
        queue.approve(rec.command_id)
    queue.on_pass_start()
    assert queue.next_for_uplink() is not None
    assert queue.next_for_uplink() is None  # cap reached


# -- service integration ------------------------------------------------------ #
def _service(faults: FaultManager, seed: int = 7):
    from comms import CommsService

    clock = MissionClock(epoch=EPOCH, time_scale=1.0)
    service = CommsService(
        clock=clock,
        faults=faults,
        station=STATION,
        orbit_period_s=5760.0,
        inclination_deg=35.0,
        first_pass_delay_s=15.0,
        link_seed=seed,
    )
    engine = TelemetryEngine(
        faults=faults,
        orbit_period_s=5760.0,
        telemetry_hz=1.0,
        seed=seed,
        epoch=EPOCH,
        dt_clip_s=600.0,
    )
    return service, engine


def _run_pass(service, engine, until_s: float = 560.0):
    events = []
    downlinked = 0
    t = 0.0
    while t < until_s:
        reading = engine.tick(1.0)
        if reading is not None:
            service.packetize(reading)
        result = service.tick(now_s=engine.elapsed)
        events.extend(result.payloads)
        downlinked += len(result.downlinked)
        t += 1.0
    return events, downlinked


def _run_pass_between(service, engine, start_s: float, end_s: float):
    events = []
    t = start_s
    while t < end_s:
        reading = engine.tick(1.0)
        if reading is not None:
            service.packetize(reading)
        events.extend(service.tick(now_s=engine.elapsed).payloads)
        t += 1.0
    return events, 0


def test_service_announces_pass_and_downlinks_telemetry():
    service, engine = _service(FaultManager(), seed=7)
    events, downlinked = _run_pass(service, engine)
    aos = [e for e in events if getattr(e, "event", None) is PassEvent.AOS]
    los = [e for e in events if getattr(e, "event", None) is PassEvent.LOS]
    assert len(aos) == 1 and aos[0].seconds_to_aos == 0.0
    assert len(los) == 1
    assert downlinked > 200
    # received == delivered + corrupted; forwarded <= received
    assert service.ingest.forwarded <= service.ingest.received
    assert service.link.stats.packets_received > 0


def test_service_full_command_lifecycle_during_pass():
    service, engine = _service(FaultManager(), seed=1)
    record = service.submit_command("ENTER_SAFE_MODE", operator="sara")
    service.approve_command(record.command_id, operator="lead")
    events, _ = _run_pass(service, engine)
    statuses = [
        e.status
        for e in events
        if getattr(e, "command_id", None) == record.command_id
    ]
    assert statuses == [
        CommandStatus.QUEUED_FOR_PASS,
        CommandStatus.UPLINKED,
        CommandStatus.EXECUTED,
    ]


def test_command_approved_during_pass_is_uplinked_immediately():
    service, engine = _service(FaultManager(), seed=1)
    # run into the middle of the pass (AOS at 15 s), then approve
    _run_pass(service, engine, until_s=60.0)
    assert service.link.state is LinkState.CONNECTED
    record = service.submit_command("THROTTLE_PAYLOAD")
    service.approve_command(record.command_id, operator="lead")
    events, _ = _run_pass_between(service, engine, 60.0, 120.0)
    statuses = [
        e.status for e in events if getattr(e, "command_id", None) == record.command_id
    ]
    assert statuses[:2] == [CommandStatus.QUEUED_FOR_PASS, CommandStatus.UPLINKED]


def test_service_reports_health_snapshot_fields():
    service, engine = _service(FaultManager(), seed=2)
    _run_pass(service, engine, until_s=100.0)
    link = service.link_status()
    assert link.simulated is True
    assert link.link_state in set(LinkState)
    assert service.next_pass_summary() is not None
    assert service.seconds_to_next_aos() is not None
