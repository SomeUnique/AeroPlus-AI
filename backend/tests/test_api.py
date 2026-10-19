"""REST + WebSocket integration tests (run against the real app + lifespan)."""

from __future__ import annotations

from datetime import datetime

from models.schemas import FaultType, loads

FAULT_TYPES = [f.value for f in FaultType]


def _next_of_type(ws, wanted: str, limit: int = 40) -> dict:
    for _ in range(limit):
        frame = ws.receive_json()
        if frame["type"] == wanted:
            return frame
    raise AssertionError(f"no '{wanted}' frame within {limit} frames")


# -- metadata ----------------------------------------------------------------- #
def test_root_lists_endpoints(client):
    body = client.get("/").json()
    assert body["websocket"] == "/ws"
    assert set(body["endpoints"]) == {
        "/health",
        "/inject-fault",
        "/reset",
        "/time-scale",
        "/ws",
    }


def test_health_reports_running_generator(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["telemetry_hz"] == 1.0
    assert body["active_faults"] == []
    # the lifespan producer task has ticked and produced a fresh reading
    assert body["last_telemetry_age_s"] is not None
    assert body["last_telemetry_age_s"] < 5.0


def test_health_reports_simulated_comms(client):
    body = client.get("/health").json()
    assert body["time_scale"] == 1.0
    assert "SIMULATED" in body["ground_station"]
    assert body["link_state"] in {"NO_CONTACT", "ACQUIRING", "CONNECTED"}
    assert body["downlink_bps"] > 0
    assert set(body["command_queue"]) == {
        "PENDING_APPROVAL",
        "APPROVED",
        "QUEUED_FOR_PASS",
        "UPLINKED",
        "EXECUTED",
    }


def test_openapi_is_generated(client):
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"]
    assert {"/health", "/inject-fault", "/reset", "/time-scale"} <= set(schema["paths"])
    fault_values = schema["components"]["schemas"]["FaultType"]["enum"]
    assert fault_values == ["battery_overheat", "solar_degradation", "signal_loss"]


# -- fault control plane ------------------------------------------------------ #
def test_inject_each_fault_type(client):
    for fault in FAULT_TYPES:
        response = client.post("/inject-fault", json={"type": fault})
        assert response.status_code == 202
        body = response.json()
        assert [f["type"] for f in body["active_faults"]] == [fault]
        assert body["active_faults"][0]["progress"] == 0.0
        assert client.get("/health").json()["active_faults"] == [fault]
        client.post("/reset")


def test_reinject_rearms_the_fault(client):
    client.post("/inject-fault", json={"type": "signal_loss"})
    response = client.post("/inject-fault", json={"type": "signal_loss"})
    assert response.status_code == 202
    assert "re-armed" in response.json()["message"]


def test_invalid_fault_type_is_rejected(client):
    response = client.post("/inject-fault", json={"type": "reactor_meltdown"})
    assert response.status_code == 422
    assert client.get("/health").json()["active_faults"] == []


def test_reset_clears_every_fault(client):
    for fault in FAULT_TYPES:
        client.post("/inject-fault", json={"type": fault})
    assert len(client.get("/health").json()["active_faults"]) == 3

    body = client.post("/reset").json()
    assert body["status"] == "reset"
    assert client.get("/health").json()["active_faults"] == []


# -- websocket ---------------------------------------------------------------- #
def test_ws_handshake_then_telemetry_at_1hz(client):
    with client.websocket_connect("/ws") as ws:
        handshake = ws.receive_json()
        assert handshake["type"] == "status_update"
        assert handshake["payload"]["status"] == "connected"
        assert loads(handshake).type == "status_update"

        stamps = []
        while len(stamps) < 4:
            frame = ws.receive_json()
            if frame["type"] != "telemetry":
                continue
            envelope = loads(frame)  # validates against the published schema
            assert envelope.type == "telemetry"
            stamps.append(datetime.fromisoformat(frame["payload"]["timestamp"]))

        deltas = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])]
        assert all(0.4 <= d <= 1.8 for d in deltas), deltas


def test_ws_seq_is_monotonic(client):
    with client.websocket_connect("/ws") as ws:
        seqs = []
        while len(seqs) < 4:
            frame = ws.receive_json()
            if frame["type"] == "telemetry":
                seqs.append(frame["seq"])
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)


def test_ws_receives_fault_and_reset_notifications(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "status_update"
        assert client.get("/health").json()["clients_connected"] >= 1

        assert client.post("/inject-fault", json={"type": "battery_overheat"}).status_code == 202
        armed = _next_of_type(ws, "status_update")
        assert armed["payload"]["status"] == "fault_armed"
        assert armed["payload"]["level"] == "warning"

        assert client.post("/reset").status_code == 200
        reset = _next_of_type(ws, "status_update")
        assert reset["payload"]["status"] == "reset"


def test_ws_survives_garbage_client_frames(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "status_update"
        ws.send_text("not json at all")
        ws.send_json({"type": "unknown_thing"})
        frame = _next_of_type(ws, "telemetry")
        assert frame["payload"]["battery_voltage"] > 0


# -- time scale --------------------------------------------------------------- #
def test_time_scale_endpoint_updates_clock_and_engine(client):
    response = client.post("/time-scale", json={"time_scale": 120})
    assert response.status_code == 200
    body = response.json()
    assert body["previous_time_scale"] == 1.0
    assert body["time_scale"] == 120.0
    health = client.get("/health").json()
    assert health["time_scale"] == 120.0


def test_time_scale_rejects_out_of_range(client):
    assert client.post("/time-scale", json={"time_scale": 0}).status_code == 422
    assert client.post("/time-scale", json={"time_scale": 9999}).status_code == 422


# -- simulated comms stream --------------------------------------------------- #
def test_ws_streams_link_and_pass_info(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "status_update"
        link = _next_of_type(ws, "link_status")
        assert loads(link).type == "link_status"
        assert link["payload"]["simulated"] is True
        pass_info = _next_of_type(ws, "pass_info")
        assert loads(pass_info).type == "pass_info"
        assert pass_info["payload"]["simulated"] is True


# -- human-in-the-loop command lifecycle -------------------------------------- #
def test_command_request_requires_approval(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "status_update"
        ws.send_json(
            {"type": "command_request", "action": "submit", "command": "ENTER_SAFE_MODE"}
        )
        submitted = _next_of_type(ws, "command_status")
        assert submitted["payload"]["status"] == "PENDING_APPROVAL"
        command_id = submitted["payload"]["command_id"]

        ws.send_json(
            {"type": "command_request", "action": "approve", "command_id": command_id}
        )
        approved = _next_of_type(ws, "command_status")
        assert approved["payload"]["command_id"] == command_id
        assert approved["payload"]["status"] == "APPROVED"
        assert approved["payload"]["approved_by"] == "operator"


def test_command_request_rejects_unknown_command(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "status_update"
        ws.send_json(
            {"type": "command_request", "action": "submit", "command": "SELF_DESTRUCT"}
        )
        update = _next_of_type(ws, "status_update")
        assert update["payload"]["status"] == "command_error"


def test_command_reject_notifies_operator(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "status_update"
        ws.send_json(
            {"type": "command_request", "action": "submit", "command": "THROTTLE_PAYLOAD"}
        )
        submitted = _next_of_type(ws, "command_status")
        command_id = submitted["payload"]["command_id"]
        ws.send_json(
            {"type": "command_request", "action": "reject", "command_id": command_id}
        )
        update = _next_of_type(ws, "status_update")
        assert update["payload"]["status"] == "command_rejected"
