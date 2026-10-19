"""AeroPulse AI - single-file FastAPI + WebSocket mission-control backend.

EVERYTHING THIS SERVER PRODUCES IS SIMULATED: telemetry, RF link, ground passes,
faults. No radio, no antenna, no real spacecraft, no real ground station.

Core loop: Detect (thresholds + rolling z-score + Isolation Forest)
        -> Decide (LLM agent when a key exists, scripted fallback otherwise)
        -> Act   (operator approval -> queued -> uplinked at the next pass).

Run:  cd backend && uvicorn main:app --port 8000
"""

from __future__ import annotations

import asyncio
import html
import json
import math
import os
import random
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Literal, Optional, Tuple

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from sklearn.ensemble import IsolationForest

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #

SENSORS: Tuple[str, ...] = (
    "battery_voltage",
    "battery_temp",
    "solar_current",
    "bus_temp",
    "signal_dbm",
)
UNITS: Dict[str, str] = {
    "battery_voltage": "V",
    "battery_temp": "degC",
    "solar_current": "A",
    "bus_temp": "degC",
    "signal_dbm": "dBm",
}
BASE: Dict[str, float] = {
    "battery_voltage": 7.42,
    "battery_temp": 21.5,
    "solar_current": 0.42,
    "bus_temp": 27.5,
    "signal_dbm": -96.0,
}
NOISE: Dict[str, float] = {
    "battery_voltage": 0.03,
    "battery_temp": 0.35,
    "solar_current": 0.015,
    "bus_temp": 0.40,
    "signal_dbm": 1.20,
}
# sensor -> (comparison, limit); crossing it fires the threshold detector
THRESH: Dict[str, Tuple[str, float]] = {
    "battery_voltage": ("lt", 6.60),
    "battery_temp": ("gt", 45.0),
    "solar_current": ("lt", 0.12),
    "bus_temp": ("gt", 50.0),
    "signal_dbm": ("lt", -112.0),
}
# critical limit: time_to_failure_min is the time to reach this value
SEVERE: Dict[str, Tuple[str, float]] = {
    "battery_voltage": ("lt", 6.10),
    "battery_temp": ("gt", 58.0),
    "solar_current": ("lt", 0.05),
    "bus_temp": ("gt", 58.0),
    "signal_dbm": ("lt", -120.0),
}
SUBSYS: Dict[str, str] = {
    "battery_voltage": "Power",
    "solar_current": "Power",
    "battery_temp": "Thermal",
    "bus_temp": "Thermal",
    "signal_dbm": "Comms",
}
SUBSYSTEMS: Tuple[str, ...] = ("Power", "Thermal", "Comms")
ALL_DETS: Tuple[str, ...] = ("threshold", "zscore", "isolation_forest")

PASS_ON_S: float = 60.0          # CONNECTED window
PASS_OFF_S: float = 60.0         # NO_CONTACT window (readings buffer onboard)
PASS_CYCLE_S: float = PASS_ON_S + PASS_OFF_S
WINDOW: int = 120                # rolling z-score window (delivered samples)
MIN_WINDOW: int = 30
CONFIRM_N: int = 3               # consecutive flagged samples before registering
CLEAN_BATCHES: int = 10          # clean batches before an incident resolves
Z_K: float = 3.0
IF_MIN_Z: float = 2.5            # Isolation Forest needs one dimension this unusual
HYS_NOISE: float = 3.0           # clear a threshold breach only this many noise-sigmas inside
RECENT_SLOPE_N: int = 12         # recent samples used for the trend estimate
SLOPE_MIN_FRAC: float = 0.05     # trend must exceed this fraction of sensor noise/s
TRAIN_N: int = 300
UPLINK_MARGIN_S: float = 15.0    # telemetry age + approval + command execution
LLM_TIMEOUT_S: float = 25.0
FAULT_RAMP_S: float = 20.0
PRIOR_TTF_MIN: Dict[str, float] = {"Power": 20.0, "Thermal": 12.0, "Comms": 60.0}
FAULT_TYPES: Tuple[str, ...] = ("battery_overheat", "solar_degradation", "signal_loss")

INDEX_PATH = Path(__file__).resolve().parent / "static" / "index.html"

DETECTOR_WEIGHT: Dict[str, float] = {
    "threshold": 0.50,
    "zscore": 0.25,
    "isolation_forest": 0.30,
}

PLAYBOOK: Dict[str, Dict[str, Any]] = {
    "Power": {
        "root_cause": (
            "The power subsystem is outside its safe band: battery bus voltage or "
            "solar array current has drifted from nominal, so the spacecraft may no "
            "longer hold a positive energy balance."
        ),
        "plain_explanation": (
            "The battery and the solar panels are not behaving as expected. If charge "
            "keeps leaving the battery faster than the panels put it in, the spacecraft "
            "will start switching loads off to survive."
        ),
        "recommendation": (
            "Switch to low-power mode now: turn off non-essential payloads, cap the "
            "charge current, and let the battery recover before the next pass."
        ),
        "command": "ENTER_LOW_POWER_MODE",
        "params": {"payload_power": "off", "charge_current_ma": 250},
    },
    "Thermal": {
        "root_cause": (
            "Battery or bus temperature is above the safe operating band: the spacecraft "
            "is rejecting less heat than it is absorbing, so temperatures keep climbing."
        ),
        "plain_explanation": (
            "The satellite is getting too hot. Electronics lose accuracy and can be "
            "damaged when they cook, so the spacecraft has to shed heat and slow down."
        ),
        "recommendation": (
            "Reduce internal dissipation and let the bus cool: stop charging, drop "
            "high-power payloads, and hold a sun-avoiding attitude until the "
            "temperature returns to band."
        ),
        "command": "THERMAL_SAFE_HOLD",
        "params": {"heaters": "off", "payload_power": "off"},
    },
    "Comms": {
        "root_cause": (
            "The RF link budget has collapsed: the received signal sits far below the "
            "modem's usable range, so telemetry and commands are being lost."
        ),
        "plain_explanation": (
            "The radio link to the ground station is too weak to carry data reliably. "
            "The spacecraft will keep working, but we cannot hear it until the link recovers."
        ),
        "recommendation": (
            "Reset and re-key the radio, drop to the robust low data rate, and keep "
            "housekeeping stored onboard until the next pass."
        ),
        "command": "RESET_RADIO_LOW_RATE",
        "params": {"radio": "reset", "mode": "low_rate"},
    },
}
SAFE_MODE_COMMAND = "ENTER_SAFE_MODE"
SAFE_MODE_PARAMS = {"mode": "safe", "shed": "all_nonessential"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fmt_clock(seconds: float) -> str:
    total = int(max(0.0, seconds))
    return "T+{:02d}:{:02d}:{:02d}".format(total // 3600, (total % 3600) // 60, total % 60)


def _baseline(t: float, f_over: float, f_solar: float, f_signal: float) -> Dict[str, float]:
    """Drifting nominal model (orbital-ish cycles) plus active SIMULATED fault effects."""
    sun = 0.5 + 0.5 * math.sin(2.0 * math.pi * t / 600.0)
    return {
        "battery_voltage": 7.42 + 0.10 * math.sin(t / 170.0) - 0.85 * f_over - 0.55 * f_solar,
        "battery_temp": 21.5 + 3.0 * math.sin(t / 230.0 + 1.0) + 46.0 * f_over,
        "solar_current": 0.42 * (0.30 + 0.70 * sun) * (1.0 - 0.96 * f_solar),
        "bus_temp": 27.5 + 3.5 * math.sin(t / 290.0 + 2.0) + 9.0 * f_over,
        "signal_dbm": -96.0 + 3.0 * math.sin(t / 95.0) - 26.0 * f_signal,
    }


def _slope(values: List[float]) -> float:
    """Least-squares slope per sample."""
    n = len(values)
    if n < 3:
        return 0.0
    xs = np.arange(n, dtype=float)
    ys = np.asarray(values, dtype=float)
    xm, ym = xs.mean(), ys.mean()
    denom = float(((xs - xm) ** 2).sum())
    return float(((xs - xm) * (ys - ym)).sum() / denom) if denom else 0.0


# --------------------------------------------------------------------------- #
# domain objects
# --------------------------------------------------------------------------- #


@dataclass
class Anomaly:
    sensor: str
    detector: str
    value: float
    detail: str
    z: Optional[float] = None


@dataclass
class Incident:
    id: str
    subsystem: str
    first_seen: float
    last_seen: float = 0.0
    status: str = "OPEN"          # OPEN | RESOLVED
    severity: str = "LOW"         # LOW | MEDIUM | HIGH | CRITICAL
    confidence: float = 0.0
    sensors: List[str] = field(default_factory=list)
    detectors: Dict[str, List[str]] = field(default_factory=dict)
    values: Dict[str, float] = field(default_factory=dict)
    z: Dict[str, float] = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)
    anomaly_count: int = 0
    clean_batches: int = 0
    agent_status: str = "PENDING"  # PENDING | ANALYZING | DONE
    analysis: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "subsystem": self.subsystem,
            "status": self.status,
            "severity": self.severity,
            "confidence": round(self.confidence, 3),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "first_seen_at": _fmt_clock(self.first_seen),
            "last_seen_at": _fmt_clock(self.last_seen),
            "sensors": list(self.sensors),
            "detectors": {k: list(v) for k, v in self.detectors.items()},
            "values": {k: round(v, 3) for k, v in self.values.items()},
            "z": {k: round(v, 2) for k, v in self.z.items()},
            "evidence": list(self.evidence),
            "anomaly_count": self.anomaly_count,
            "agent_status": self.agent_status,
            "analysis": self.analysis,
        }


@dataclass
class Command:
    id: str
    incident_id: str
    subsystem: str
    name: str
    params: Dict[str, Any]
    rationale: str
    status: str = "PENDING_APPROVAL"  # -> QUEUED_FOR_PASS -> UPLINKED
    created_at: float = 0.0
    approved_at: Optional[float] = None
    uplinked_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "incident_id": self.incident_id,
            "subsystem": self.subsystem,
            "name": self.name,
            "params": self.params,
            "rationale": self.rationale,
            "status": self.status,
            "created_at": _fmt_clock(self.created_at),
            "approved_at": _fmt_clock(self.approved_at) if self.approved_at is not None else None,
            "uplinked_at": _fmt_clock(self.uplinked_at) if self.uplinked_at is not None else None,
        }


class Hub:
    """Fan-out of JSON events to every open /ws client."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def join(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    async def leave(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception:
                self.clients.discard(ws)


class FaultIn(BaseModel):
    fault: Literal["battery_overheat", "solar_degradation", "signal_loss"]


# --------------------------------------------------------------------------- #
# mission state machine
# --------------------------------------------------------------------------- #


class Mission:
    def __init__(self) -> None:
        self.rng = random.Random(20261019)
        self.iforest, self.if_cut = self._train_iforest()
        self.hub = Hub()
        self.gen = 0
        self._tasks: set[asyncio.Task[None]] = set()
        self.reset()

    # -- lifecycle ------------------------------------------------------------

    def reset(self) -> None:
        self.gen += 1
        self.seq = 0
        self.mission_t = 0.0
        self.was_connected = True
        self.faults: Dict[str, float] = {}          # name -> started at mission_t
        self.buffer: List[Dict[str, Any]] = []       # onboard store-and-forward
        self.history: Deque[Dict[str, Any]] = deque(maxlen=600)
        self.windows: Dict[str, Deque[float]] = {s: deque(maxlen=WINDOW) for s in SENSORS}
        self.streaks: Dict[Tuple[str, str], int] = {}
        self.thr_active: Dict[str, bool] = {s: False for s in SENSORS}
        self.incidents: List[Incident] = []
        self.open: Dict[str, Incident] = {}
        self.commands: Dict[str, Command] = {}
        self._inc_seq = 0
        self._cmd_seq = 0
        self.last_values: Dict[str, float] = dict(BASE)
        self.agent_mode = self._provider_mode()

    @staticmethod
    def _provider_mode() -> str:
        if os.getenv("ANTHROPIC_API_KEY"):
            return "LLM:anthropic"
        if os.getenv("OPENAI_API_KEY"):
            return "LLM:openai"
        return "OFFLINE FALLBACK"

    # -- training -------------------------------------------------------------

    @staticmethod
    def _train_iforest() -> Tuple[IsolationForest, float]:
        """Isolation Forest fitted on ~300 nominal SIMULATED samples at startup."""
        rng = np.random.default_rng(7)
        t = rng.uniform(0.0, 600.0, TRAIN_N)
        base = [
            _baseline(float(ti), 0.0, 0.0, 0.0) for ti in t
        ]
        x = np.array(
            [
                [b[s] + float(rng.normal(0.0, NOISE[s])) for s in SENSORS]
                for b in base
            ],
            dtype=float,
        )
        model = IsolationForest(
            n_estimators=120, contamination="auto", random_state=42, n_jobs=1
        )
        model.fit(x)
        cut = float(np.min(model.score_samples(x))) - 0.01
        return model, cut

    # -- link / buffer --------------------------------------------------------

    @property
    def _phase(self) -> float:
        return self.mission_t % PASS_CYCLE_S

    def _connected(self) -> bool:
        return self._phase < PASS_ON_S

    def next_pass_s(self) -> float:
        phase = self._phase
        return 0.0 if phase < PASS_ON_S else PASS_CYCLE_S - phase

    def pass_remaining_s(self) -> float:
        phase = self._phase
        return PASS_ON_S - phase if phase < PASS_ON_S else 0.0

    def response_deadline_s(self) -> float:
        """Seconds until the soonest contact window we can still uplink a command in.

        In a pass with enough time left this is 0 (act now). If the pass is about to
        end, or we are in a gap, the wait is until the next pass starts.
        """
        if self._connected():
            return 0.0 if self.pass_remaining_s() >= UPLINK_MARGIN_S else PASS_CYCLE_S - self._phase
        return self.next_pass_s()

    def link_payload(self) -> Dict[str, Any]:
        return {
            "link_state": "CONNECTED" if self._connected() else "NO_CONTACT",
            "next_pass_s": round(self.next_pass_s(), 1),
            "pass_remaining_s": round(self.pass_remaining_s(), 1),
            "deadline_s": round(self.response_deadline_s(), 1),
            "buffer_depth": len(self.buffer),
            "cycle_pos_s": round(self._phase, 1),
            "simulated": True,
        }

    def active_faults(self) -> List[str]:
        return sorted(self.faults.keys())

    def _strengths(self) -> Dict[str, float]:
        return {
            name: min(1.0, (self.mission_t - start) / FAULT_RAMP_S)
            for name, start in self.faults.items()
        }

    # -- sampling -------------------------------------------------------------

    def sample(self) -> Dict[str, float]:
        s = self._strengths()
        nominal = _baseline(
            self.mission_t,
            s.get("battery_overheat", 0.0),
            s.get("solar_degradation", 0.0),
            s.get("signal_loss", 0.0),
        )
        reading = {
            key: nominal[key] + self.rng.gauss(0.0, NOISE[key]) for key in SENSORS
        }
        return {k: round(v, 3) for k, v in reading.items()}

    # -- events ---------------------------------------------------------------

    async def emit(self, event: str, **data: Any) -> None:
        self.seq += 1
        message: Dict[str, Any] = {
            "type": event,
            "seq": self.seq,
            "mission_t": round(self.mission_t, 1),
            "clock": _fmt_clock(self.mission_t),
            "ts": _now(),
            "simulated": True,
        }
        message.update(data)
        await self.hub.broadcast(message)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "mission_t": round(self.mission_t, 1),
            "clock": _fmt_clock(self.mission_t),
            "ts": _now(),
            "simulated": True,
            "agent_mode": self.agent_mode,
            "active_faults": self.active_faults(),
            "link": self.link_payload(),
            "config": {
                "sensors": list(SENSORS),
                "units": UNITS,
                "thresholds": {s: {"op": o, "limit": l} for s, (o, l) in THRESH.items()},
                "subsystems": SUBSYS,
                "pass_on_s": PASS_ON_S,
                "pass_off_s": PASS_OFF_S,
                "window_s": WINDOW,
            },
            "history": list(self.history),
            "incidents": [i.to_dict() for i in self.incidents],
            "commands": [c.to_dict() for c in self.commands.values()],
        }

    # -- detection ------------------------------------------------------------

    def _iforest_flag(self, values: Dict[str, float]) -> Tuple[bool, str, float]:
        """Multivariate outlier check.

        A raw Isolation Forest score on 5 sensors raises too many one-off alarms, so a
        flag is only accepted when at least one dimension is itself >= IF_MIN_Z sigma
        unusual (rolling z-score, or noise-sigma before the window is full).
        """
        x = np.array([[values[s] for s in SENSORS]], dtype=float)
        score = float(self.iforest.score_samples(x)[0])
        if score >= self.if_cut:
            return False, "", score
        dev: Dict[str, float] = {}
        for s in SENSORS:
            w = self.windows[s]
            if len(w) >= MIN_WINDOW:
                sd = float(np.std(w))
                dev[s] = (values[s] - float(np.mean(w))) / sd if sd > 1e-9 else 0.0
            else:
                dev[s] = abs(values[s] - BASE[s]) / NOISE[s]
        worst = max(dev, key=lambda k: abs(dev[k]))
        if abs(dev[worst]) < IF_MIN_Z:
            return False, "", score
        return True, worst, score

    def detect(self, values: Dict[str, float]) -> List[Anomaly]:
        hits: Dict[str, List[str]] = {s: [] for s in SENSORS}
        znow: Dict[str, float] = {}
        for s in SENSORS:
            v = values[s]
            w = self.windows[s]
            w.append(v)
            op, lim = THRESH[s]
            margin = HYS_NOISE * NOISE[s]
            crossed = v < lim if op == "lt" else v > lim
            cleared = v >= lim + margin if op == "lt" else v <= lim - margin
            if self.thr_active[s]:
                if cleared:
                    self.thr_active[s] = False
            elif crossed:
                self.thr_active[s] = True
            if self.thr_active[s]:
                hits[s].append("threshold")
            if len(w) >= MIN_WINDOW:
                mean = float(np.mean(w))
                sd = float(np.std(w))
                if sd > 1e-9:
                    z = (v - mean) / sd
                    znow[s] = z
                    if abs(z) >= Z_K:
                        hits[s].append("zscore")
        flagged, worst, score = self._iforest_flag(values)
        if flagged:
            hits[worst].append("isolation_forest")

        anomalies: List[Anomaly] = []
        for s in SENSORS:
            for det in ALL_DETS:
                key = (s, det)
                if det in hits[s]:
                    self.streaks[key] = self.streaks.get(key, 0) + 1
                else:
                    self.streaks[key] = 0
                if self.streaks[key] < CONFIRM_N:
                    continue
                op, lim = THRESH[s]
                if det == "threshold":
                    detail = f"{values[s]:.3f} {UNITS[s]} {'<' if op == 'lt' else '>'} {lim} {UNITS[s]}"
                elif det == "zscore":
                    detail = f"z = {znow.get(s, 0.0):+.2f} over {WINDOW}s rolling window"
                else:
                    detail = f"Isolation Forest outlier score = {score:.3f}"
                anomalies.append(
                    Anomaly(
                        sensor=s,
                        detector=det,
                        value=values[s],
                        detail=detail,
                        z=znow.get(s),
                    )
                )
        return anomalies

    def _excess(self, sensor: str, value: float) -> float:
        op, lim = THRESH[sensor]
        sop, slim = SEVERE[sensor]
        span = abs(slim - lim) or 1.0
        return (value - lim) / span if sop == "gt" else (lim - value) / span

    def _score(self, inc: Incident) -> None:
        dets = {d for v in inc.detectors.values() for d in v}
        severe = any(
            (inc.values[s] > SEVERE[s][1]) if SEVERE[s][0] == "gt" else (inc.values[s] < SEVERE[s][1])
            for s in inc.sensors
            if s in inc.values
        )
        score = 0
        if "threshold" in dets:
            score += 2
        if severe:
            score += 3
        if "zscore" in dets:
            score += 1
        if "isolation_forest" in dets:
            score += 1
        inc.severity = (
            "CRITICAL" if score >= 5 else "HIGH" if score >= 3 else "MEDIUM" if score >= 2 else "LOW"
        )
        conf = 1.0
        for d in dets:
            conf *= 1.0 - DETECTOR_WEIGHT.get(d, 0.2)
        inc.confidence = min(0.98, (1.0 - conf) + (0.10 if severe else 0.0))

    def _evidence(self, inc: Incident) -> List[str]:
        out: List[str] = []
        for s in inc.sensors:
            dets = inc.detectors.get(s, [])
            if not dets:
                continue
            out.append(
                f"{s} = {inc.values.get(s, float('nan')):.3f} {UNITS[s]} via {', '.join(dets)}"
            )
            if s in inc.z:
                out.append(f"{s} rolling z-score = {inc.z[s]:+.2f} (window {WINDOW}s)")
        fault = self.active_faults()
        if fault:
            out.append(f"active SIMULATED fault(s): {', '.join(fault)}")
        out.append(
            f"SIMULATED ground link {'CONNECTED' if self._connected() else 'NO_CONTACT'}, "
            f"next pass in {self.next_pass_s():.0f}s"
        )
        return out[:8]

    def _worst_sensor(self, inc: Incident) -> str:
        scored = [
            (self._excess(s, inc.values.get(s, 0.0)), s)
            for s in inc.sensors
            if s in inc.values
        ]
        if not scored:
            return inc.sensors[0] if inc.sensors else SENSORS[0]
        return max(scored)[1]

    def time_to_failure(self, inc: Incident) -> Tuple[float, str]:
        """Minutes until the driving sensor reaches its critical (SEVERE) limit.

        Uses the recent trend only when it is clearly steeper than sensor noise,
        otherwise falls back to a declared per-subsystem prior (method reported in
        the analysis so the number is never mistaken for a measurement).
        """
        sensor = self._worst_sensor(inc)
        v = inc.values.get(sensor, BASE[sensor])
        sop, slim = SEVERE[sensor]
        vals = [h["values"][sensor] for h in self.history if sensor in h["values"]][-RECENT_SLOPE_N:]
        if (sop == "gt" and v >= slim) or (sop == "lt" and v <= slim):
            return 0.5, "imminent"
        slope = _slope(vals) if len(vals) >= RECENT_SLOPE_N // 2 else 0.0
        worsening = (sop == "gt" and slope > 0) or (sop == "lt" and slope < 0)
        if worsening and abs(slope) >= SLOPE_MIN_FRAC * NOISE[sensor]:
            dt = (slim - v) / slope
            if dt > 0:
                return round(min(240.0, max(0.5, dt / 60.0)), 1), "slope"
        return PRIOR_TTF_MIN[inc.subsystem], "prior"

    def ground_can_respond(self, ttf_min: float) -> bool:
        """Can the ground still uplink an approved command before the failure?"""
        return ttf_min * 60.0 >= self.response_deadline_s() + UPLINK_MARGIN_S

    async def deliver(self, readings: List[Dict[str, Any]]) -> None:
        """Run detection on every delivered sample, then group by subsystem."""
        if not readings:
            return
        hits: Dict[str, List[Anomaly]] = defaultdict(list)
        for r in readings:
            values = r["values"]
            self.last_values = dict(values)
            for a in self.detect(values):
                hits[SUBSYS[a.sensor]].append(a)

        for sub, items in hits.items():
            inc = self.open.get(sub)
            fresh = inc is None
            if inc is None:
                self._inc_seq += 1
                inc = Incident(
                    id=f"INC-{self._inc_seq:03d}",
                    subsystem=sub,
                    first_seen=self.mission_t,
                    last_seen=self.mission_t,
                )
                self.open[sub] = inc
                self.incidents.append(inc)
            else:
                inc.clean_batches = 0
            for a in items:
                inc.anomaly_count += 1
                inc.last_seen = self.mission_t
                if a.sensor not in inc.sensors:
                    inc.sensors.append(a.sensor)
                dets = inc.detectors.setdefault(a.sensor, [])
                if a.detector not in dets:
                    dets.append(a.detector)
                inc.values[a.sensor] = a.value
                if a.z is not None:
                    inc.z[a.sensor] = a.z
            self._score(inc)
            inc.evidence = self._evidence(inc)
            if fresh:
                inc.agent_status = "ANALYZING"
                task = asyncio.create_task(self._run_agent(inc))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
                await self.emit("incident_new", incident=inc.to_dict())
            else:
                await self.emit("incident_update", incident=inc.to_dict())

        for sub, inc in list(self.open.items()):
            if sub in hits:
                continue
            inc.clean_batches += 1
            if inc.clean_batches >= CLEAN_BATCHES:
                inc.status = "RESOLVED"
                inc.evidence = self._evidence(inc)
                self.open.pop(sub, None)
                await self.emit("incident_resolved", incident=inc.to_dict())

    # -- agent ----------------------------------------------------------------

    def _context(self) -> Dict[str, Any]:
        return {
            "latest": {k: round(v, 3) for k, v in self.last_values.items()},
            "link": self.link_payload(),
            "thresholds": {
                s: {"op": o, "limit": l, "critical": SEVERE[s][1]}
                for s, (o, l) in THRESH.items()
            },
            "active_faults": self.active_faults(),
            "agent_mode_at_call": self.agent_mode,
        }

    async def _run_agent(self, inc: Incident) -> None:
        gen = self.gen
        snapshot = inc.to_dict()
        ctx = self._context()
        analysis: Optional[Dict[str, Any]] = None
        mode = "OFFLINE FALLBACK"
        if self._provider_mode().startswith("LLM"):
            try:
                analysis = await asyncio.wait_for(
                    asyncio.to_thread(_llm_analyze, snapshot, ctx), LLM_TIMEOUT_S
                )
            except Exception:
                analysis = None
            if analysis is not None:
                mode = str(analysis.pop("_mode", self._provider_mode()))
        if analysis is None:
            analysis = _scripted_analysis(snapshot, ctx)
        analysis = _postprocess(analysis, snapshot, ctx, inc)
        analysis["agent_mode"] = mode
        if gen != self.gen or inc.status != "OPEN":
            return
        self.agent_mode = mode
        inc.analysis = analysis
        inc.agent_status = "DONE"
        await self.emit("agent", incident_id=inc.id, analysis=analysis)

        cmd = self._propose_command(inc, analysis)
        await self.emit("command", command=cmd.to_dict())

    def _propose_command(self, inc: Incident, analysis: Dict[str, Any]) -> Command:
        play = PLAYBOOK[inc.subsystem]
        urgent = inc.severity == "CRITICAL" or not analysis["can_ground_respond_in_time"]
        name = SAFE_MODE_COMMAND if urgent else str(play["command"])
        params = dict(SAFE_MODE_PARAMS) if urgent else dict(play["params"])
        params["incident_id"] = inc.id
        self._cmd_seq += 1
        cmd = Command(
            id=f"CMD-{self._cmd_seq:03d}",
            incident_id=inc.id,
            subsystem=inc.subsystem,
            name=name,
            params=params,
            rationale=analysis["recommendation"],
            created_at=self.mission_t,
        )
        self.commands[cmd.id] = cmd
        return cmd

    async def uplink_queued(self) -> None:
        """Transmit approved commands, but only inside a usable contact window."""
        if self.response_deadline_s() > 0.0:
            return
        for cmd in self.commands.values():
            if cmd.status != "QUEUED_FOR_PASS":
                continue
            cmd.status = "UPLINKED"
            cmd.uplinked_at = self.mission_t
            await self.emit("command", command=cmd.to_dict())

    # -- main loop ------------------------------------------------------------

    async def run(self) -> None:
        while True:
            t0 = time.monotonic()
            try:
                await self._tick()
            except Exception as exc:  # keep the sampler alive no matter what
                await self.emit("error", message=f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(max(0.0, 1.0 - (time.monotonic() - t0)))

    async def _tick(self) -> None:
        self.mission_t += 1.0
        reading = {"t": round(self.mission_t, 1), "values": self.sample()}
        was = self.was_connected
        now_connected = self._connected()
        await self.emit("link", **self.link_payload())
        if now_connected and not was:                       # AOS: burst the buffer
            buffered = self.buffer[:]
            self.buffer.clear()
            if buffered:
                await self.emit("burst", count=len(buffered), readings=buffered)
                self.history.extend(buffered)
                await self.deliver(buffered)
            await self.uplink_queued()
        elif now_connected:                                  # live pass
            await self.emit("telemetry", **reading)
            self.history.append(reading)
            await self.deliver([reading])
            await self.uplink_queued()
        else:                                                # LOS: buffer onboard
            self.buffer.append(reading)
        self.was_connected = now_connected

    # -- operations -----------------------------------------------------------

    def inject_fault(self, name: str) -> None:
        self.faults[name] = self.mission_t

    async def reset_mission(self) -> None:
        self.reset()
        await self.emit("reset")
        await self.emit("snapshot", **self.snapshot())

# --------------------------------------------------------------------------- #
# agent: LLM path + scripted fallback + post-processing
# --------------------------------------------------------------------------- #

_LLM_SYSTEM = (
    "You are AeroPulse AI, an operations co-pilot for a university CubeSat team. "
    "All telemetry you are shown is SIMULATED. Be honest and calibrated: false alarms "
    "are possible. Use plain language a student engineer understands, no unexplained "
    "jargon. Reply with ONE JSON object only, no markdown fences, with exactly these "
    "keys: severity (LOW|MEDIUM|HIGH|CRITICAL), root_cause (string), "
    "plain_explanation (string), recommendation (string), confidence (number 0..1), "
    "time_to_failure_min (number), evidence (array of short strings)."
)


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    blob = text.strip()
    if blob.startswith("```"):
        blob = blob.split("```")[1]
        if blob.lstrip().lower().startswith("json"):
            blob = blob.lstrip()[4:]
    start, end = blob.find("{"), blob.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(blob[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _llm_analyze(incident: Dict[str, Any], ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    prompt = (
        "Incident (SIMULATED CubeSat telemetry):\n"
        + json.dumps({"incident": incident, "context": ctx}, indent=2)
        + "\nDiagnose the root cause, estimate time to failure in minutes, and propose "
        "the safest operator action. Remember the reply must be a single JSON object."
    )
    key_a, key_o = os.getenv("ANTHROPIC_API_KEY"), os.getenv("OPENAI_API_KEY")
    if key_a:
        import anthropic

        model = os.getenv("AEROPULSE_ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
        client = anthropic.Anthropic(api_key=key_a)
        msg = client.messages.create(
            model=model,
            max_tokens=800,
            system=_LLM_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        data = _parse_json(text)
        if data is None:
            return None
        data["_mode"] = f"LLM:anthropic/{model}"
        return data
    if key_o:
        from openai import OpenAI

        model = os.getenv("AEROPULSE_OPENAI_MODEL", "gpt-4o-mini")
        client = OpenAI(api_key=key_o)
        resp = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )
        data = _parse_json(resp.choices[0].message.content or "")
        if data is None:
            return None
        data["_mode"] = f"LLM:openai/{model}"
        return data
    return None


def _scripted_analysis(incident: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic rule-based Decide stage used when no LLM key is configured."""
    play = PLAYBOOK[incident["subsystem"]]
    faults = ctx.get("active_faults") or []
    root = str(play["root_cause"])
    if faults:
        root = (
            f"Active SIMULATED fault(s) [{', '.join(faults)}] are driving this event. " + root
        )
    return {
        "root_cause": root,
        "plain_explanation": str(play["plain_explanation"]),
        "recommendation": str(play["recommendation"]),
        "confidence": float(incident.get("confidence", 0.5)),
        "time_to_failure_min": None,  # resolved from telemetry slope in _postprocess
        "evidence": list(incident.get("evidence", [])),
    }


def _postprocess(
    analysis: Dict[str, Any],
    incident: Dict[str, Any],
    ctx: Dict[str, Any],
    inc_obj: "Incident",
) -> Dict[str, Any]:
    """Enforce the honest fields: severity, timing, ground-response and safe mode."""
    mission: Mission = MISSION
    sub = str(incident["subsystem"])
    play = PLAYBOOK[sub]
    ttf, method = mission.time_to_failure(inc_obj)
    raw_ttf = analysis.get("time_to_failure_min")
    if isinstance(raw_ttf, (int, float)) and method == "prior" and 0.05 <= float(raw_ttf) <= 240.0:
        ttf, method = round(float(raw_ttf), 1), "llm_estimate"
    can_respond = mission.ground_can_respond(ttf)

    def _text(key: str, fallback: str) -> str:
        value = analysis.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return fallback

    recommendation = _text("recommendation", str(play["recommendation"]))
    command_name = (
        SAFE_MODE_COMMAND
        if (not can_respond or str(incident["severity"]) == "CRITICAL")
        else str(play["command"])
    )
    deadline = float(ctx["link"].get("deadline_s", ctx["link"]["next_pass_s"]))
    if not can_respond:
        window = (
            "no usable contact window left in this pass"
            if deadline > ctx["link"]["next_pass_s"]
            else f"next contact in {deadline:.0f} s"
        )
        recommendation = (
            f"Ground cannot respond before the estimated failure (~{ttf:g} min; {window}). "
            f"Command the spacecraft into ONBOARD SAFE MODE now: '{command_name}' is approved "
            "by the operator and uplinked at the next contact, while the spacecraft protects "
            "itself autonomously."
        )
    llm_ev = analysis.get("evidence")
    llm_ev = [str(x) for x in llm_ev] if isinstance(llm_ev, list) else []
    base_ev = [str(x) for x in incident.get("evidence", [])]
    evidence = list(dict.fromkeys(base_ev + llm_ev))[:10] or base_ev
    try:
        confidence = round(float(analysis.get("confidence", incident.get("confidence", 0.5))), 3)
    except (TypeError, ValueError):
        confidence = round(float(incident.get("confidence", 0.5)), 3)
    return {
        "severity": incident["severity"],
        "subsystem": sub,
        "root_cause": _text("root_cause", str(play["root_cause"])),
        "plain_explanation": _text("plain_explanation", str(play["plain_explanation"])),
        "recommendation": recommendation,
        "confidence": max(0.02, min(0.99, confidence)),
        "time_to_failure_min": ttf,
        "ttf_method": method,
        "can_ground_respond_in_time": can_respond,
        "proposed_command": command_name,
        "next_pass_s": ctx["link"]["next_pass_s"],
        "response_deadline_s": deadline,
        "evidence": evidence,
        "human_approval_required": True,
    }


# --------------------------------------------------------------------------- #
# printable incident report
# --------------------------------------------------------------------------- #


def _report_html(m: "Mission") -> str:
    esc = html.escape
    rows: List[str] = []
    for inc in m.incidents:
        a = inc.analysis or {}
        evidence = "".join(f"<li>{esc(str(e))}</li>" for e in inc.evidence) or "<li>-</li>"
        analysis = (
            f"<p><b>Root cause:</b> {esc(str(a.get('root_cause', 'pending')))}</p>"
            f"<p><b>In plain words:</b> {esc(str(a.get('plain_explanation', 'pending')))}</p>"
            f"<p><b>Recommendation:</b> {esc(str(a.get('recommendation', 'pending')))}</p>"
            f"<table class='kv'><tr><th>Severity</th><td>{esc(inc.severity)}</td>"
            f"<th>Confidence</th><td>{inc.confidence:.0%}</td>"
            f"<th>Time to failure</th><td>{esc(str(a.get('time_to_failure_min', '-')))} min</td>"
            f"<th>Ground can respond</th><td>{'yes' if a.get('can_ground_respond_in_time') else 'no - onboard safe mode'}</td></tr>"
            f"<tr><th>Agent mode</th><td>{esc(str(a.get('agent_mode', m.agent_mode)))}</td>"
            f"<th>Sensors</th><td>{esc(', '.join(inc.sensors))}</td>"
            f"<th>Detect count</th><td>{inc.anomaly_count}</td>"
            f"<th>Status</th><td>{esc(inc.status)}</td></tr></table>"
            f"<details open><summary>Evidence</summary><ul>{evidence}</ul></details>"
        )
        rows.append(
            f"<section><h2>{esc(inc.id)} - {esc(inc.subsystem)} "
            f"<span class='chip sev-{esc(inc.severity.lower())}'>{esc(inc.severity)}</span></h2>"
            f"<p class='meta'>{_fmt_clock(inc.first_seen)} -> {_fmt_clock(inc.last_seen)} "
            f"(SIMULATED mission time)</p>{analysis}</section>"
        )
    cmd_rows = "".join(
        f"<tr><td>{esc(c.id)}</td><td>{esc(c.incident_id)}</td><td>{esc(c.name)}</td>"
        f"<td>{esc(c.status)}</td><td>{esc(str(c.approved_at or '-'))}</td>"
        f"<td>{esc(str(c.uplinked_at or '-'))}</td><td>{esc(c.rationale)}</td></tr>"
        for c in m.commands.values()
    ) or "<tr><td colspan='7'>No commands were proposed.</td></tr>"
    faults = ", ".join(m.active_faults()) or "none active"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>AeroPulse AI - Incident Report (SIMULATED)</title>
<style>
 body {{ font-family: Georgia, 'Times New Roman', serif; color:#111; margin:2rem auto; max-width:60rem; line-height:1.5; }}
 .banner {{ background:#7c2d12; color:#fff; padding:.6rem 1rem; letter-spacing:.12em; font-family:ui-monospace,monospace; font-size:.85rem; }}
 h1 {{ font-size:1.6rem; margin:.8rem 0 .2rem; }} h2 {{ font-size:1.05rem; margin:1.4rem 0 .2rem; border-bottom:1px solid #ccc; padding-bottom:.2rem; }}
 .meta {{ color:#555; font-size:.85rem; margin:.1rem 0 .6rem; }}
 .chip {{ font-size:.7rem; padding:.1rem .45rem; border-radius:.5rem; font-family:ui-monospace,monospace; vertical-align:middle; }}
 .sev-critical {{ background:#7f1d1d; color:#fff; }} .sev-high {{ background:#b45309; color:#fff; }}
 .sev-medium {{ background:#a16207; color:#fff; }} .sev-low {{ background:#3f6212; color:#fff; }}
 table {{ border-collapse:collapse; width:100%; font-size:.85rem; margin:.6rem 0; }}
 th, td {{ border:1px solid #bbb; padding:.35rem .5rem; text-align:left; vertical-align:top; }}
 th {{ background:#f1f1f1; }} .kv th {{ width:9rem; }}
 ul {{ font-size:.85rem; margin:.2rem 0 .4rem 1.2rem; }}
 .sign {{ margin-top:2rem; display:flex; gap:3rem; font-size:.85rem; }}
 .sign div {{ border-top:1px solid #333; padding-top:.3rem; width:16rem; }}
 footer {{ margin-top:2rem; font-size:.78rem; color:#444; border-top:1px solid #999; padding-top:.6rem; }}
 @media print {{ .noprint {{ display:none; }} body {{ margin:1rem; }} }}
</style></head><body>
<div class="banner">SIMULATED DATA - SOFTWARE DEMONSTRATION ONLY - NO REAL SPACECRAFT OR GROUND STATION INVOLVED</div>
<h1>AeroPulse AI - Incident Report</h1>
<p class="meta">Generated {esc(_now())} | SIMULATED mission clock {_fmt_clock(m.mission_t)} |
 Ground link {'CONNECTED' if m._connected() else 'NO_CONTACT'} | Active SIMULATED faults: {esc(faults)} |
 Agent mode: <b>{esc(m.agent_mode)}</b></p>
<p class="meta">Detect (thresholds + rolling z-score + Isolation Forest) -&gt; Decide (LLM or scripted
 fallback) -&gt; Act (operator approval only). Incidents recorded: {len(m.incidents)}.
 Commands proposed: {len(m.commands)}.</p>
{''.join(rows) or '<p>No incidents were recorded during this run.</p>'}
<h1 style="font-size:1.1rem">Uplink queue</h1>
<table><tr><th>ID</th><th>Incident</th><th>Command</th><th>Status</th><th>Approved</th>
<th>Uplinked</th><th>Rationale</th></tr>{cmd_rows}</table>
<div class="sign"><div>Operator (print, sign)</div><div>Reviewed by</div></div>
<footer><b>Limitations:</b> every number on this page is produced by a SIMULATED model on one
 machine; false alarms are possible and no detector has been tested on real hardware. Agent
 output may be a scripted fallback when no LLM key is configured. Commands are never executed
 without explicit operator approval.</footer>
</body></html>"""


# --------------------------------------------------------------------------- #
# app
# --------------------------------------------------------------------------- #

MISSION = Mission()


@asynccontextmanager
async def lifespan(_: FastAPI) -> Any:
    task = asyncio.create_task(MISSION.run())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="AeroPulse AI (SIMULATED)", lifespan=lifespan)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(INDEX_PATH)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await MISSION.hub.join(ws)
    try:
        await ws.send_json({"type": "snapshot", **MISSION.snapshot()})
        while True:
            text = await ws.receive_text()
            if text.strip().lower() == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await MISSION.hub.leave(ws)


@app.post("/inject-fault")
async def inject_fault(body: FaultIn) -> JSONResponse:
    MISSION.inject_fault(body.fault)
    await MISSION.emit(
        "fault_injected",
        fault=body.fault,
        active=MISSION.active_faults(),
        note="SIMULATED fault ramping in over "
        f"{FAULT_RAMP_S:.0f}s - no real hardware affected",
    )
    return JSONResponse(
        {
            "ok": True,
            "fault": body.fault,
            "simulated": True,
            "active": MISSION.active_faults(),
        }
    )


@app.post("/reset")
async def reset() -> JSONResponse:
    await MISSION.reset_mission()
    return JSONResponse({"ok": True, "simulated": True, "message": "mission state reset"})


@app.post("/approve/{command_id}")
async def approve(command_id: str) -> JSONResponse:
    cmd = MISSION.commands.get(command_id)
    if cmd is None:
        return JSONResponse({"ok": False, "error": "unknown command"}, status_code=404)
    if cmd.status != "PENDING_APPROVAL":
        return JSONResponse(
            {"ok": False, "error": f"command is {cmd.status}", "command": cmd.to_dict()},
            status_code=409,
        )
    cmd.status = "QUEUED_FOR_PASS"
    cmd.approved_at = MISSION.mission_t
    await MISSION.emit("command", command=cmd.to_dict())
    await MISSION.uplink_queued()
    return JSONResponse({"ok": True, "command": cmd.to_dict()})


@app.get("/report", include_in_schema=False)
async def report() -> HTMLResponse:
    return HTMLResponse(_report_html(MISSION))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
