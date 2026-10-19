"""Tests for the detection layer (rule / z-score / rate + incidents + service)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from detection import DetectionService
from detection.config import Subsystem, SystemStatus
from detection.detector import Detector
from detection.incidents import IncidentManager
from detection.models import Anomaly
from detection.config import DetectorType
from models.schemas import (
    AnomalyMessage,
    DetectorKind,
    FaultType,
    PayloadMode,
    Sensor,
    Severity,
    StatusUpdateMessage,
    TelemetryReading,
)
from services.faults import FaultManager
from services.telemetry import TelemetryEngine


def reading(**overrides) -> TelemetryReading:
    base = dict(
        timestamp=datetime.now(timezone.utc),
        battery_voltage=27.6,
        battery_temp=30.0,
        solar_current=6.5,
        bus_temp=25.0,
        signal_strength=-75.0,
        gyro_rate=0.003,
        payload_power=2.5,
        in_eclipse=False,
        sun_fraction=1.0,
        payload_mode=PayloadMode.IDLE,
    )
    base.update(overrides)
    return TelemetryReading(**base)


# -- rule --------------------------------------------------------------------- #
def test_rule_fires_on_high_battery_temp():
    svc = DetectionService()
    out = svc.process(reading(battery_temp=56.0), now=0.0)
    anomalies = [p for p in out if isinstance(p, AnomalyMessage)]
    assert anomalies, "expected a rule anomaly"
    a = anomalies[0]
    assert a.sensor is Sensor.BATTERY_TEMP
    assert a.detector_type is DetectorKind.RULE
    assert a.severity is Severity.CRITICAL
    assert a.subsystem == Subsystem.THERMAL.value


def test_rule_solar_is_gated_by_sunlight():
    svc = DetectionService()
    # In eclipse solar_current is ~0; the low-current rule must stay silent.
    out = svc.process(reading(solar_current=0.02, sun_fraction=0.0, in_eclipse=True), now=0.0)
    assert not [p for p in out if isinstance(p, AnomalyMessage)]


# -- z-score ------------------------------------------------------------------ #
def test_zscore_detects_solar_degradation_residual():
    det = Detector()
    # Warm the model-residual window with nominal full-sun samples.
    for i in range(40):
        det.feed(reading(solar_current=6.5 + (i % 3) * 0.03), now=float(i))
    out = []
    for i in range(40, 46):
        out += det.feed(reading(solar_current=6.5 * 0.85), now=float(i))
    hits = [a for a in out if a.detector_type is DetectorType.ZSCORE]
    assert hits, "degraded array should trip the residual z-score"
    assert all(a.sensor == "solar_current" for a in hits)


def test_zscore_quiet_on_flat_nominal_solar():
    det = Detector()
    out = []
    for i in range(120):
        out += det.feed(reading(solar_current=6.5 + (i % 4) * 0.02), now=float(i))
    assert not [a for a in out if a.detector_type is DetectorType.ZSCORE]


# -- rate of change ----------------------------------------------------------- #
def test_roc_detects_thermal_ramp():
    det = Detector()
    out = []
    for i in range(20):
        out += det.feed(reading(battery_temp=30.0 + 0.5 * i), now=float(i))
    hits = [a for a in out if a.detector_type is DetectorType.RATE and a.sensor == "battery_temp"]
    assert hits, "a sustained fast ramp should trip rate-of-change"
    assert hits[0].extra["slope"] > 0.2


def test_roc_ignores_attitude_slew_burst():
    det = Detector()
    out = []
    for i in range(60):
        gyro = 0.09 if 20 <= i < 26 else 0.003
        out += det.feed(reading(gyro_rate=gyro), now=float(i))
    assert not [a for a in out if a.sensor == "gyro_rate"], "nominal slew must not alarm"


# -- incidents ---------------------------------------------------------------- #
def _anom(sub=Subsystem.THERMAL, sev="warning", sensor="battery_temp") -> Anomaly:
    return Anomaly(sensor=sensor, value=1.0, subsystem=sub, severity_hint=sev, description="x")


def test_incident_debounces_and_escalates():
    mgr = IncidentManager(debounce_sec=10.0, expiry_sec=90.0)
    emitted, changes = mgr.feed([_anom(sev="warning")])
    assert len(emitted) == 1
    assert changes and changes[0][2] is SystemStatus.WARNING

    # Same severity within the debounce window -> not re-broadcast.
    emitted, _ = mgr.feed([_anom(sev="warning")])
    assert emitted == []
    # Escalation always breaks through the debounce.
    emitted, changes = mgr.feed([_anom(sev="critical")])
    assert len(emitted) == 1
    assert changes and changes[0][2] is SystemStatus.CRITICAL


def test_incident_groups_by_subsystem_and_assigns_id():
    mgr = IncidentManager()
    emitted, _ = mgr.feed([_anom(sub=Subsystem.COMMS, sensor="signal_strength")])
    assert emitted[0].incident_id and emitted[0].incident_id.startswith("inc_")
    assert Subsystem.COMMS in mgr.incidents


def test_incident_expires_back_to_nominal():
    mgr = IncidentManager(debounce_sec=0.0, expiry_sec=30.0)
    mgr.feed([_anom(sev="critical")])
    assert mgr.status[Subsystem.THERMAL] is SystemStatus.CRITICAL
    # Age the incident past the expiry window, then a quiet feed resolves it.
    mgr.incidents[Subsystem.THERMAL].updated_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    _, changes = mgr.feed([])
    assert changes and changes[0][2] is SystemStatus.NOMINAL
    assert mgr.status[Subsystem.THERMAL] is SystemStatus.NOMINAL


# -- service ------------------------------------------------------------------ #
def test_service_maps_detector_metadata():
    svc = DetectionService()
    for i in range(40):
        svc.process(reading(solar_current=6.5 + (i % 3) * 0.03), now=float(i))
    out = []
    for i in range(40, 48):
        out += svc.process(reading(solar_current=6.5 * 0.85), now=float(i))
    msg = next(p for p in out if isinstance(p, AnomalyMessage))
    assert msg.detector_type is DetectorKind.ZSCORE
    assert msg.subsystem == Subsystem.POWER.value
    assert msg.incident_id and msg.incident_id.startswith("inc_")


def test_service_emits_detection_status_update():
    svc = DetectionService()
    for i in range(40):
        svc.process(reading(solar_current=6.5 + (i % 3) * 0.03), now=float(i))
    out = []
    for i in range(40, 46):
        out += svc.process(reading(solar_current=6.5 * 0.85), now=float(i))
    status = next(p for p in out if isinstance(p, StatusUpdateMessage))
    assert status.status == "Power_WARNING"
    assert status.level is Severity.WARNING


def test_service_reset_clears_state():
    svc = DetectionService()
    for i in range(40):
        svc.process(reading(solar_current=6.5 + (i % 3) * 0.03), now=float(i))
    svc.reset()
    assert svc.anomalies_raised == 0
    assert all(v == "NOMINAL" for v in svc.status_snapshot().values())


# -- end to end --------------------------------------------------------------- #
def test_battery_overheat_detected_end_to_end():
    engine = TelemetryEngine(faults=FaultManager(), seed=1234)
    svc = DetectionService()
    for i in range(300):
        svc.process(engine.tick(1.0), now=engine.elapsed)

    engine.faults.activate(FaultType.BATTERY_OVERHEAT)
    detected = False
    for _ in range(150):
        reading_now = engine.tick(1.0)
        if reading_now is None:
            continue
        for payload in svc.process(reading_now, now=engine.elapsed):
            if isinstance(payload, AnomalyMessage) and payload.subsystem == Subsystem.THERMAL.value:
                detected = True
                break
        if detected:
            break
    assert detected, "battery_overheat should be detected within 150 s"
    assert svc.status_snapshot()["Thermal"] in ("WARNING", "CRITICAL")


def test_signal_loss_detected_end_to_end():
    engine = TelemetryEngine(faults=FaultManager(), seed=7)
    svc = DetectionService()
    for _ in range(300):
        svc.process(engine.tick(1.0), now=engine.elapsed)

    engine.faults.activate(FaultType.SIGNAL_LOSS)
    detected = False
    for _ in range(150):
        r = engine.tick(1.0)
        if r is None:
            continue
        for payload in svc.process(r, now=engine.elapsed):
            if isinstance(payload, AnomalyMessage) and payload.subsystem == Subsystem.COMMS.value:
                detected = True
                break
        if detected:
            break
    assert detected, "signal_loss should be detected within 150 s"


def test_health_reports_subsystems(client):
    body = client.get("/health").json()
    assert set(body["subsystems"]) == {"Power", "Thermal", "Comms", "Payload", "ADCS"}
    assert body["anomalies_raised"] == 0
