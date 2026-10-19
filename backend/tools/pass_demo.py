"""End-to-end pass demo (SIMULATED).

Connects to a running AeroPulse AI backend over /ws and prints one full
ground-station pass as it happens:

    NEXT_PASS countdown -> AOS -> telemetry downlink (through the SIMULATED RF
    link + ground ingest) -> LOS, plus a human-in-the-loop uplink command going
    PENDING_APPROVAL -> APPROVED -> QUEUED_FOR_PASS -> UPLINKED -> EXECUTED.

Everything printed here is simulated: no real satellite, radio or ground
station is involved.

Usage (start the server first, from the backend/ directory):

    # terminal 1
    AEROPULSE_TIME_SCALE=60 uvicorn app.main:app        # PowerShell: $env:AEROPULSE_TIME_SCALE=60

    # terminal 2
    python -m tools.pass_demo --time-scale 60

    python -m tools.pass_demo --url ws://127.0.0.1:8000/ws --no-command
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any, Dict, Optional

import httpx
import websockets

DEFAULT_WS = "ws://127.0.0.1:8000/ws"
DEFAULT_HTTP = "http://127.0.0.1:8000"


def log(message: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


async def set_time_scale(http_url: str, scale: float) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(f"{http_url}/time-scale", json={"time_scale": scale})
            response.raise_for_status()
        log(f"time scale set to {scale:.0f}x (simulated seconds per wall second)")
    except Exception as exc:  # noqa: BLE001 - demo keeps going without a scale change
        log(f"could not set time scale ({exc}); continuing at server default")


def _fmt_reading(payload: Dict[str, Any]) -> str:
    return (
        f"V={payload['battery_voltage']:.2f}V "
        f"Tbatt={payload['battery_temp']:.1f}C "
        f"sol={payload['solar_current']:.2f}A "
        f"rssi={payload['signal_strength']:.1f}dBm "
        f"{'eclipse' if payload['in_eclipse'] else 'sunlit'}"
    )


class PassReport:
    def __init__(self) -> None:
        self.telemetry = 0
        self.first_reading: Optional[Dict[str, Any]] = None
        self.last_reading: Optional[Dict[str, Any]] = None
        self.link: Dict[str, Any] = {}
        self.command_states: list[str] = []
        self.anomalies = 0
        self.beacons = 0

    def summary(self) -> str:
        lines = [
            "==================== SIMULATED PASS SUMMARY ====================",
            f"  telemetry frames received : {self.telemetry}",
            f"  onboard beacons received  : {self.beacons}",
            f"  anomalies raised          : {self.anomalies}",
        ]
        if self.first_reading and self.last_reading:
            lines.append(f"  first reading : {_fmt_reading(self.first_reading)}")
            lines.append(f"  last  reading : {_fmt_reading(self.last_reading)}")
        if self.link:
            lines.append(
                "  link counters : "
                f"received={self.link.get('packets_received')} "
                f"lost={self.link.get('packets_lost')} "
                f"crc_failures={self.link.get('crc_failures')} "
                f"buffer_depth={self.link.get('buffer_depth')}"
            )
        if self.command_states:
            lines.append(f"  uplink command: {' -> '.join(self.command_states)}")
        lines.append("=================================================================")
        lines.append("  NOTE: all data above is SIMULATED - no real hardware involved.")
        return "\n".join(lines)


async def run_demo(
    ws_url: str,
    http_url: str,
    *,
    time_scale: Optional[float],
    send_command: bool,
    max_wall_s: float,
) -> int:
    if time_scale is not None:
        await set_time_scale(http_url, time_scale)

    report = PassReport()
    saw_aos = False
    saw_los = False
    last_link_state: Optional[str] = None
    last_link_print = 0.0
    last_pass_print = 0.0

    async with websockets.connect(ws_url, open_timeout=10.0) as ws:
        log(f"connected to {ws_url} (SIMULATED mission stream)")
        command_id: Optional[str] = None
        started = time.monotonic()

        if send_command:
            await ws.send(json.dumps({
                "type": "command_request",
                "action": "submit",
                "command": "ENTER_SAFE_MODE",
                "params": {"reason": "thermal demo"},
                "operator": "demo",
            }))
            log("  (operator) submitted ENTER_SAFE_MODE for approval")

        while time.monotonic() - started < max_wall_s:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max_wall_s)
            except asyncio.TimeoutError:
                break
            frame = json.loads(raw)
            kind = frame["type"]
            payload = frame["payload"]

            if kind == "status_update":
                log(f"  status_update   {payload['level']:8s} {payload['status']}: {payload['message']}")
                if payload["status"] == "onboard_beacon":
                    report.beacons += 1
            elif kind == "link_status":
                report.link = payload
                state = payload["link_state"]
                now = time.monotonic()
                if state != last_link_state or now - last_link_print > 2.0:
                    log(
                        f"  link_status     {state:11s} quality={payload['signal_quality']:.2f}  "
                        f"buffer={payload['buffer_depth']}"
                    )
                    last_link_state = state
                    last_link_print = now
            elif kind == "pass_info":
                event = payload["event"]
                window = payload.get("window") or {}
                if event == "next_pass":
                    now = time.monotonic()
                    if now - last_pass_print > 2.0:
                        countdown = payload.get("seconds_to_aos")
                        log(
                            f"  pass_info       NEXT PASS in {countdown:.0f} sim-s "
                            f"(max el {window.get('max_elevation_deg')} deg, "
                            f"duration {window.get('duration_s'):.0f} sim-s)"
                        )
                        last_pass_print = now
                elif event == "aos":
                    saw_aos = True
                    log(f"  pass_info       *** AOS *** contact started (window {window.get('pass_id')})")
                elif event == "los":
                    saw_los = True
                    nxt = payload.get("seconds_to_aos")
                    log(f"  pass_info       *** LOS *** contact ended (next pass in {nxt:.0f} sim-s)")
            elif kind == "telemetry":
                report.telemetry += 1
                if report.first_reading is None:
                    report.first_reading = payload
                    log(f"  telemetry       first frame: {_fmt_reading(payload)}")
                elif report.telemetry % 25 == 0:
                    log(f"  telemetry       {report.telemetry:4d} frames  last: {_fmt_reading(payload)}")
                report.last_reading = payload
            elif kind == "anomaly":
                report.anomalies += 1
                log(
                    f"  anomaly         {payload['severity']:8s} {payload['sensor']} "
                    f"conf={payload['confidence']:.2f} via {payload.get('detector_type')} - {payload['description']}"
                )
            elif kind == "command_status":
                state = payload["status"]
                report.command_states.append(state)
                log(f"  command_status  {state:17s} {payload['command']} - {payload['detail']}")
                if command_id is None:
                    command_id = payload["command_id"]
                    if send_command and state == "PENDING_APPROVAL":
                        await ws.send(json.dumps({
                            "type": "command_request",
                            "action": "approve",
                            "command_id": command_id,
                            "operator": "demo",
                        }))
                        log("  (operator) approving the queued command (human-in-the-loop)")
            elif kind in ("agent_thinking", "agent_action", "report_ready"):
                log(f"  {kind:15s} {json.dumps(payload)[:100]}")

            if saw_los:
                break

    print()
    print(report.summary())
    if not saw_aos:
        log("no AOS observed within the window - try a larger --time-scale or --max-wall-s")
        return 2
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_WS, help="WebSocket URL (default: %(default)s)")
    parser.add_argument("--http", default=DEFAULT_HTTP, help="HTTP base URL for /time-scale")
    parser.add_argument("--time-scale", type=float, default=None, help="POST /time-scale before connecting")
    parser.add_argument("--no-command", action="store_true", help="skip the uplink command demo")
    parser.add_argument("--max-wall-s", type=float, default=90.0, help="stop after this many wall seconds")
    args = parser.parse_args(argv)

    try:
        return asyncio.run(
            run_demo(
                args.url,
                args.http,
                time_scale=args.time_scale,
                send_command=not args.no_command,
                max_wall_s=args.max_wall_s,
            )
        )
    except (OSError, websockets.exceptions.WebSocketException) as exc:
        log(f"could not reach the backend: {exc} (is uvicorn running?)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
