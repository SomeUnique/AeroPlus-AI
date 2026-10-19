"""Behaviour tests for the synthetic telemetry engine and fault injection."""

from __future__ import annotations

import pytest

from models.schemas import FaultType, PayloadMode
from services.faults import FaultManager
from services.telemetry import TelemetryEngine


def make_engine(seed: int = 42, faults: FaultManager | None = None) -> TelemetryEngine:
    return TelemetryEngine(faults=faults or FaultManager(), telemetry_hz=1.0, seed=seed)


def run(engine: TelemetryEngine, seconds: int, dt: float = 1.0):
    return [engine.tick(dt) for _ in range(seconds)]


# -- generation --------------------------------------------------------------- #
def test_produces_one_reading_per_tick_without_drops_when_nominal():
    engine = make_engine()
    readings = run(engine, 60)
    assert len(readings) == 60
    assert all(r is not None for r in readings)
    assert engine.stats.generated == 60
    assert engine.stats.dropped == 0


def test_deterministic_for_a_given_seed():
    a, b = make_engine(seed=9), make_engine(seed=9)
    rows_a = [r for r in run(a, 300)]
    rows_b = [r for r in run(b, 300)]
    fields = [f for f in rows_a[0].model_dump() if f != "timestamp"]
    for left, right in zip(rows_a, rows_b):
        assert {k: getattr(left, k) for k in fields} == {k: getattr(right, k) for k in fields}


def test_different_seeds_diverge():
    a, b = make_engine(seed=1), make_engine(seed=2)
    run(a, 300)
    run(b, 300)
    assert a.last_reading.signal_strength != b.last_reading.signal_strength


def test_reading_carries_all_sensors_in_physical_ranges():
    engine = make_engine()
    for row in run(engine, 5000):
        assert 18.0 <= row.battery_voltage <= 33.0
        assert 5.0 <= row.battery_temp <= 70.0
        assert 0.0 <= row.solar_current <= 7.5
        assert 5.0 <= row.bus_temp <= 40.0
        assert -125.0 <= row.signal_strength <= -50.0
        assert 0.0 <= row.gyro_rate <= 0.35
        assert 0.0 <= row.payload_power <= 55.0
        assert 0.0 <= row.sun_fraction <= 1.0
        assert row.payload_mode in PayloadMode


def test_values_are_noisy_not_constant():
    engine = make_engine()
    rows = [r for r in run(engine, 300)]
    assert len({r.signal_strength for r in rows}) > 100
    assert len({r.battery_voltage for r in rows}) > 50


# -- orbital cycle ------------------------------------------------------------ #
def test_eclipse_fraction_matches_configuration():
    engine = make_engine()
    rows = [r for r in run(engine, 12000)]
    eclipse_ratio = sum(r.in_eclipse for r in rows) / len(rows)
    assert 0.30 <= eclipse_ratio <= 0.40


def test_solar_current_tracks_illumination():
    engine = make_engine()
    rows = [r for r in run(engine, 12000)]
    lit = [r for r in rows if not r.in_eclipse]
    shadow = [r for r in rows if r.in_eclipse]

    assert sum(r.solar_current for r in lit) / len(lit) > 5.5
    assert max(r.solar_current for r in shadow) < 3.5
    # measured current never exceeds the illuminated model by more than noise
    for row in rows:
        assert row.solar_current <= 6.6 * row.sun_fraction + 0.6


def test_temperature_follows_the_day_night_cycle():
    engine = make_engine()
    rows = [r for r in run(engine, 12000)]
    lit = [r for r in rows if not r.in_eclipse]
    shadow = [r for r in rows if r.in_eclipse]
    assert sum(r.bus_temp for r in lit) / len(lit) > sum(r.bus_temp for r in shadow) / len(shadow) + 5.0


def test_battery_voltage_cycles_with_charge_and_discharge():
    engine = make_engine()
    rows = [r for r in run(engine, 12000)]
    # the orbital cycle must produce a visible voltage swing (> 0.6 V)
    assert max(r.battery_voltage for r in rows) - min(r.battery_voltage for r in rows) > 0.6


def test_payload_duty_cycle_produces_distinct_modes():
    engine = make_engine()
    rows = [r for r in run(engine, 1200)]
    by_mode = {}
    for row in rows:
        by_mode.setdefault(row.payload_mode, []).append(row.payload_power)
    assert set(by_mode) == {PayloadMode.IDLE, PayloadMode.IMAGING, PayloadMode.DOWNLINK}
    assert 40.0 <= max(by_mode[PayloadMode.IMAGING]) <= 55.0
    idle = sorted(by_mode[PayloadMode.IDLE])
    assert idle[0] < 4.0  # converter settles to standby power
    assert idle[len(idle) // 2] < 6.0


# -- faults ------------------------------------------------------------------- #
def test_battery_overheat_ramps_temperature_and_sags_voltage():
    engine = make_engine()
    run(engine, 180)
    before = engine.last_reading

    engine.faults.activate(FaultType.BATTERY_OVERHEAT)
    run(engine, 90)
    after = engine.last_reading

    assert after.battery_temp - before.battery_temp > 15.0
    assert before.battery_voltage - after.battery_voltage > 1.5
    assert engine.faults.progress(FaultType.BATTERY_OVERHEAT) == pytest.approx(1.0)


def test_solar_degradation_decays_output():
    engine = make_engine(seed=3)
    while engine.last_reading is None or engine.last_reading.sun_fraction < 0.95:
        engine.tick(1.0)
    before = engine.last_reading.solar_current

    engine.faults.activate(FaultType.SOLAR_DEGRADATION)
    while (
        engine.last_reading.in_eclipse
        or engine.last_reading.sun_fraction < 0.95
        or engine.faults.progress(FaultType.SOLAR_DEGRADATION) < 1.0
    ):
        engine.tick(1.0)
    after = engine.last_reading.solar_current

    assert before > 6.0
    assert after < 1.0
    assert after < before * 0.2


def test_signal_loss_drops_rssi_and_causes_packet_loss():
    engine = make_engine(seed=5)
    run(engine, 60)
    before = engine.last_reading.signal_strength

    engine.faults.activate(FaultType.SIGNAL_LOSS)
    rows = run(engine, 200)
    delivered = [r for r in rows if r is not None]
    lost = sum(r is None for r in rows)

    assert engine.last_reading.signal_strength < before - 15.0
    assert lost > 50
    assert sum(r.signal_strength for r in delivered) / len(delivered) < before - 10.0


def test_reset_clears_fault_effects():
    engine = make_engine(seed=5)
    engine.faults.activate(FaultType.SIGNAL_LOSS)
    run(engine, 120)
    assert engine.faults.active
    assert engine.faults.effects().packet_loss_prob > 0.4

    engine.faults.clear()
    engine.reset_physics()
    assert not engine.faults.active
    assert engine.faults.effects().packet_loss_prob == 0.0
    assert engine.faults.effects().solar_multiplier == 1.0
    assert engine.faults.effects().battery_temp_add_c == 0.0
    assert engine.stats.dropped > 0  # history preserved for health reporting


def test_effects_accumulate_when_faults_stack():
    faults = FaultManager()
    faults.activate(FaultType.BATTERY_OVERHEAT)
    faults.activate(FaultType.SIGNAL_LOSS)
    run(make_engine(faults=faults), 200)
    effects = faults.effects()
    assert effects.battery_temp_add_c > 40.0
    assert effects.packet_loss_prob > 0.4
