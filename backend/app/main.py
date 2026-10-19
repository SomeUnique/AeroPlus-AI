"""AeroPulse AI - mission control backend (Detect -> Decide -> Act).

FastAPI app: REST control plane + the /ws mission stream.

Data path (all SIMULATED):

    TelemetryEngine -> Packetizer -> onboard buffer -> (pass) -> RF link
        -> ground ingest -> WS telemetry -> detection (Detect) -> agent (Decide)
        -> operator-approved uplink commands (Act)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from agent import AgentService
from comms import (
    CommandError,
    CommsService,
    GroundStation,
    LinkConfig,
    PacketType,
    TelemetryPacket,
)
from detection import DetectionService
from models.schemas import (
    AgentActionMessage,
    AgentThinkingMessage,
    AnomalyMessage,
    CommandAction,
    CommandStatusMessage,
    HealthResponse,
    InjectFaultRequest,
    InjectFaultResponse,
    LinkStatusMessage,
    PassInfoMessage,
    ReportReadyMessage,
    ResetResponse,
    Severity,
    StatusSource,
    StatusUpdateMessage,
    TelemetryReading,
    TimeScaleRequest,
    TimeScaleResponse,
    load_client_frame,
    make_agent_action,
    make_agent_thinking,
    make_anomaly,
    make_command_status,
    make_link_status,
    make_pass_info,
    make_report_ready,
    make_status_update,
    make_telemetry,
)
from services.clock import MissionClock
from services.faults import FAULT_SPECS, FaultManager
from services.hub import ConnectionHub
from services.telemetry import TelemetryEngine

logger = logging.getLogger("aeropulse")
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)


def _make_publisher(hub: ConnectionHub):
    """Wrap any validated payload in the right envelope and fan it out."""

    async def publish(payload: object) -> None:
        if isinstance(payload, AnomalyMessage):
            message = make_anomaly(payload, hub.next_seq())
        elif isinstance(payload, StatusUpdateMessage):
            message = make_status_update(payload, hub.next_seq())
        elif isinstance(payload, LinkStatusMessage):
            message = make_link_status(payload, hub.next_seq())
        elif isinstance(payload, PassInfoMessage):
            message = make_pass_info(payload, hub.next_seq())
        elif isinstance(payload, CommandStatusMessage):
            message = make_command_status(payload, hub.next_seq())
        elif isinstance(payload, AgentThinkingMessage):
            await hub.broadcast(make_agent_thinking(payload, hub.next_seq()))
            return
        elif isinstance(payload, AgentActionMessage):
            await hub.broadcast(make_agent_action(payload, hub.next_seq()))
            return
        elif isinstance(payload, ReportReadyMessage):
            await hub.broadcast(make_report_ready(payload, hub.next_seq()))
            return
        else:
            return
        await hub.broadcast(message)

    return publish


def _beacon_status(packet: TelemetryPacket) -> StatusUpdateMessage:
    """Turn a received onboard BEACON frame into a plain-language critical alert."""
    field = packet.payload
    return StatusUpdateMessage(
        source=StatusSource.SYSTEM,
        level=Severity.CRITICAL,
        status="onboard_beacon",
        message=(
            f"Onboard limit breached: {field.get('sensor')} = {field.get('value')} "
            f"(limit {field.get('limit')}, {field.get('direction')}). "
            f"Priority beacon #{packet.seq} received from the spacecraft."
        ),
    )


async def _telemetry_loop(app: FastAPI) -> None:
    """Single producer for all clients: one reading/interval, drift-free pacing.

    Each reading is packetized onboard, and only packets the ground actually
    receives during a simulated pass are forwarded into the Detect/Decide path.
    """
    engine: TelemetryEngine = app.state.engine
    comms: CommsService = app.state.comms
    clock: MissionClock = app.state.clock
    hub: ConnectionHub = app.state.hub
    detection: DetectionService = app.state.detection
    agent = app.state.agent
    publish = app.state.publish
    interval = settings.telemetry_interval
    loop = asyncio.get_running_loop()
    last = loop.time()

    while True:
        started = loop.time()
        wall_dt = max(0.0, started - last)
        last = started

        # Produce readings at the *simulated* telemetry cadence regardless of the
        # demo time scale: one reading per sim-second. At 60x that is ~60 readings
        # per wall second, so the downlink and detectors see a 1 Hz mission even
        # when the wall clock is accelerated.
        sim_dt_total = wall_dt * clock.time_scale
        steps = max(1, min(600, round(sim_dt_total / interval))) if sim_dt_total > 0 else 1
        dt_each = sim_dt_total / steps if sim_dt_total > 0 else 0.0

        for _ in range(steps):
            reading = engine.tick(dt_each)
            if reading is not None:
                comms.packetize(reading)

        result = comms.tick(now_s=engine.elapsed)
        for payload in result.payloads:
            await publish(payload)

        for packet in result.downlinked:
            if packet.packet_type is PacketType.BEACON:
                await publish(_beacon_status(packet))
                continue
            downlinked = TelemetryReading.model_validate(packet.payload)
            await hub.broadcast(make_telemetry(downlinked, hub.next_seq()))
            detected = detection.process(downlinked, now=downlinked.timestamp.timestamp())
            for payload in detected:
                await publish(payload)
            agent.observe(detected, context=downlinked)

        await asyncio.sleep(max(0.0, interval - (loop.time() - started)))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.started_at = time.monotonic()
    app.state.clock = MissionClock(time_scale=settings.time_scale)
    app.state.hub = ConnectionHub()
    app.state.faults = FaultManager()
    app.state.detection = DetectionService()
    app.state.publish = _make_publisher(app.state.hub)
    app.state.agent = AgentService(
        enabled=settings.agent_enabled,
        step_delay_s=settings.agent_step_delay_s,
        action_delay_s=settings.agent_action_delay_s,
        report_delay_s=settings.agent_report_delay_s,
    )
    app.state.agent.set_publisher(app.state.publish)
    app.state.engine = TelemetryEngine(
        faults=app.state.faults,
        telemetry_hz=settings.telemetry_hz,
        orbit_period_s=settings.orbit_period_s,
        eclipse_fraction=settings.eclipse_fraction,
        seed=settings.seed,
        epoch=app.state.clock.epoch,
        dt_clip_s=max(30.0, 2.0 * settings.time_scale),
    )
    station = GroundStation(
        name=settings.ground_station_name,
        lat_deg=settings.ground_station_lat_deg,
        lon_deg=settings.ground_station_lon_deg,
        min_elevation_deg=settings.ground_station_min_elevation_deg,
    )
    app.state.comms = CommsService(
        clock=app.state.clock,
        faults=app.state.faults,
        station=station,
        orbit_period_s=settings.orbit_period_s,
        inclination_deg=settings.orbit_inclination_deg,
        first_pass_delay_s=settings.first_pass_delay_s,
        buffer_capacity=settings.onboard_buffer_capacity,
        link_config=LinkConfig(
            downlink_bps=settings.downlink_bps,
            acquiring_s=settings.link_acquiring_s,
            base_packet_loss=settings.link_base_packet_loss,
            base_crc_error=settings.link_base_crc_error,
        ),
        max_commands_per_pass=settings.max_commands_per_pass,
        command_ack_delay_s=settings.command_ack_delay_s,
        link_seed=settings.seed,
    )
    if settings.agent_enabled:
        await app.state.agent.start()
    app.state.loop_task = asyncio.create_task(_telemetry_loop(app), name="telemetry-loop")
    first_pass = app.state.comms.first_pass
    logger.info(
        "%s v%s - telemetry %.2f Hz, orbit %.0f s, time scale %.0fx, agent %s",
        settings.app_name,
        settings.version,
        settings.telemetry_hz,
        settings.orbit_period_s,
        settings.time_scale,
        "enabled" if settings.agent_enabled else "disabled",
    )
    logger.info(
        "SIMULATED station '%s' - first pass AOS in %.1f s, lasts %.0f s, peak %.1f deg",
        station.name,
        first_pass.aos_s,
        first_pass.duration_s(),
        first_pass.max_elevation_deg,
    )
    try:
        yield
    finally:
        app.state.loop_task.cancel()
        with suppress(asyncio.CancelledError):
            await app.state.loop_task
        await app.state.agent.stop()
        logger.info("telemetry loop stopped")


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description="AI co-pilot for satellite mission control: Detect -> Decide -> Act.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["meta"])
async def root() -> dict:
    return {
        "service": settings.app_name,
        "version": settings.version,
        "websocket": "/ws",
        "endpoints": ["/health", "/inject-fault", "/reset", "/time-scale", "/ws"],
    }


@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health(request: Request) -> HealthResponse:
    state = request.app.state
    comms: CommsService = state.comms
    last = state.engine.last_reading
    age: Optional[float] = (
        (time.time() - last.timestamp.timestamp()) if last is not None else None
    )
    next_pass = comms.next_pass_summary()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.version,
        uptime_s=round(time.monotonic() - state.started_at, 3),
        clients_connected=state.hub.count,
        readings_generated=state.engine.stats.generated,
        readings_dropped=state.engine.stats.dropped,
        frames_delivered=state.hub.frames_delivered,
        last_telemetry_age_s=round(age, 3) if age is not None else None,
        active_faults=state.faults.active_types,
        telemetry_hz=settings.telemetry_hz,
        subsystems=state.detection.status_snapshot(),
        anomalies_raised=state.detection.anomalies_raised,
        agent_enabled=settings.agent_enabled,
        agent_incidents_handled=state.agent.incidents_handled,
        agent_active_incidents=state.agent.active_incidents,
        agent_actions_taken=state.agent.actions_taken,
        agent_reports_generated=state.agent.reports_generated,
        agent_thinking_steps=state.agent.thinking_steps,
        time_scale=state.clock.time_scale,
        ground_station=settings.ground_station_name,
        link_state=comms.link.state,
        signal_quality=round(comms.link.signal_quality, 4),
        buffer_depth=comms.buffer.depth,
        packets_received=comms.link.stats.packets_received,
        packets_lost=comms.link.stats.packets_lost,
        crc_failures=comms.link.stats.crc_failures,
        packets_forwarded=comms.ingest.forwarded,
        last_contact_time=comms.link.last_contact_time,
        next_pass_aos=state.clock.datetime_at(next_pass.aos_s) if next_pass else None,
        seconds_to_next_aos=comms.seconds_to_next_aos(),
        next_pass_max_elevation_deg=(
            round(next_pass.max_elevation_deg, 2) if next_pass else None
        ),
        downlink_bps=settings.downlink_bps,
        command_queue=comms.queue.counts(),
    )


@app.post(
    "/inject-fault",
    response_model=InjectFaultResponse,
    status_code=202,
    tags=["faults"],
)
async def inject_fault(request: Request, body: InjectFaultRequest) -> InjectFaultResponse:
    """Arm a fault. It develops gradually from this moment until /reset."""
    state = request.app.state
    restarted = state.faults.activate(body.type)
    spec = FAULT_SPECS[body.type]
    verb = "re-armed" if restarted else "armed"
    message = f"Fault {verb}: {spec.title} - {spec.description}"

    await state.hub.broadcast(
        make_status_update(
            StatusUpdateMessage(
                source=StatusSource.OPERATOR,
                level=Severity.WARNING,
                status="fault_armed",
                message=message,
            ),
            state.hub.next_seq(),
        )
    )
    logger.warning("%s (ramp %.0fs)", message, spec.ramp_seconds)
    return InjectFaultResponse(message=message, active_faults=state.faults.states())


@app.post("/reset", response_model=ResetResponse, tags=["faults"])
async def reset(request: Request) -> ResetResponse:
    """Clear every fault and restore the spacecraft to its nominal state.

    Mission time, the orbit and the RF link continue where they left off - the
    reset restores physics/health, it does not rewind the simulation.
    """
    state = request.app.state
    cleared = state.faults.clear()
    # Let the agent close any open incidents (emit their reports) before we wipe state.
    if settings.agent_enabled:
        state.agent.observe(
            state.detection.recovery_notices(), context=state.engine.last_reading
        )
        try:
            await asyncio.wait_for(state.agent.idle(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("agent did not drain before reset timeout")
    state.engine.reset_physics()
    state.detection.reset()
    state.agent.reset()
    message = f"Cleared {cleared} fault(s); spacecraft restored to nominal."
    await state.hub.broadcast(
        make_status_update(
            StatusUpdateMessage(
                source=StatusSource.OPERATOR,
                level=Severity.INFO,
                status="reset",
                message=message,
            ),
            state.hub.next_seq(),
        )
    )
    logger.info(message)
    return ResetResponse(message=message, readings_regenerated=state.engine.stats.generated)


@app.post("/time-scale", response_model=TimeScaleResponse, tags=["meta"])
async def set_time_scale(request: Request, body: TimeScaleRequest) -> TimeScaleResponse:
    """Accelerate the simulated mission for a demo (1x real time .. 600x)."""
    state = request.app.state
    previous, current = state.clock.set_time_scale(body.time_scale)
    # Keep the engine's integration step ahead of the accelerated tick so it is
    # never silently clamped.
    state.engine.dt_clip_s = max(30.0, 2.0 * current)
    message = f"Time scale {previous:.0f}x -> {current:.0f}x (simulated seconds per wall second)."
    await state.hub.broadcast(
        make_status_update(
            StatusUpdateMessage(
                source=StatusSource.OPERATOR,
                level=Severity.INFO,
                status="time_scale",
                message=message,
            ),
            state.hub.next_seq(),
        )
    )
    logger.info(message)
    return TimeScaleResponse(
        message=message, previous_time_scale=previous, time_scale=current
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Mission stream.

    Server -> client: every message type in ``models.schemas.WS_MESSAGE_MODELS``
    (first frame is a ``status_update`` handshake; then SIMULATED link_status /
    pass_info / telemetry during passes, plus detection + agent messages).
    Client -> server: ``{"type": "command_request", "action": "submit"|"approve"|"reject"}``
    for human-in-the-loop uplink commands.
    """
    state = websocket.app.state
    hub: ConnectionHub = state.hub
    comms: CommsService = state.comms
    await hub.connect(websocket)
    try:
        await hub.send(
            websocket,
            make_status_update(
                StatusUpdateMessage(
                    source=StatusSource.SYSTEM,
                    level=Severity.INFO,
                    status="connected",
                    message=(
                        f"Stream established. Telemetry {settings.telemetry_hz:.0f} Hz, "
                        f"orbit {settings.orbit_period_s:.0f} s, time scale "
                        f"{state.clock.time_scale:.0f}x, simulated station "
                        f"'{settings.ground_station_name}'."
                    ),
                ),
                hub.next_seq(),
            ),
        )
        # Give the operator the current SIMULATED link state + next pass at once.
        await hub.send(websocket, make_link_status(comms.link_status(), hub.next_seq()))
        snapshot = comms.pass_info_snapshot()
        if snapshot is not None:
            await hub.send(websocket, make_pass_info(snapshot, hub.next_seq()))
        while True:
            raw = await websocket.receive_text()
            await _handle_client_frame(state, raw)
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(websocket)


async def _handle_client_frame(state, raw: str) -> None:
    """Dispatch a client -> server frame (command_request)."""
    try:
        frame = load_client_frame(json.loads(raw))
    except Exception:  # noqa: BLE001 - never let a bad client frame kill the socket
        logger.debug("ignoring malformed client frame: %.120s", raw)
        return

    comms: CommsService = state.comms
    publish = state.publish
    try:
        if frame.action is CommandAction.SUBMIT:
            if not frame.command:
                raise CommandError("submit requires a 'command' name")
            record = comms.submit_command(
                frame.command, frame.params, operator=frame.operator
            )
            await publish(record)
        elif frame.action is CommandAction.APPROVE:
            if not frame.command_id:
                raise CommandError("approve requires 'command_id'")
            await publish(comms.approve_command(frame.command_id, operator=frame.operator))
        elif frame.action is CommandAction.REJECT:
            if not frame.command_id:
                raise CommandError("reject requires 'command_id'")
            rejected = comms.reject_command(frame.command_id)
            await publish(
                StatusUpdateMessage(
                    source=StatusSource.OPERATOR,
                    level=Severity.INFO,
                    status="command_rejected",
                    message=f"Command {rejected.command} ({rejected.command_id}) rejected.",
                )
            )
    except CommandError as exc:
        await publish(
            StatusUpdateMessage(
                source=StatusSource.OPERATOR,
                level=Severity.WARNING,
                status="command_error",
                message=str(exc),
            )
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=False,
    )
