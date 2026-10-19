"""Fault injection.

A fault is *armed* via POST /inject-fault, then develops over a simulation
ramp (`ramp_seconds`) until it reaches `progress == 1.0` and stays there until
POST /reset. Progress advances on **simulated** time (the engine's ``dt``), so
faults stay correct under test fast-forwarding and are identical to wall time
at the nominal 1 Hz rate.

The engine reads `effects` every tick and folds them into the nominal physics,
so faults are gradual and physically plausible rather than step changes -
exactly what the detector will have to catch later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from models.schemas import FaultState, FaultType


@dataclass(frozen=True)
class FaultSpec:
    type: FaultType
    ramp_seconds: float
    title: str
    description: str


FAULT_SPECS: Dict[FaultType, FaultSpec] = {
    FaultType.BATTERY_OVERHEAT: FaultSpec(
        type=FaultType.BATTERY_OVERHEAT,
        ramp_seconds=90.0,
        title="Battery overheat",
        description="Cell temperature rises continuously while the pack voltage sags.",
    ),
    FaultType.SOLAR_DEGRADATION: FaultSpec(
        type=FaultType.SOLAR_DEGRADATION,
        ramp_seconds=120.0,
        title="Solar array degradation",
        description="Array output current decays toward ~12% of nominal.",
    ),
    FaultType.SIGNAL_LOSS: FaultSpec(
        type=FaultType.SIGNAL_LOSS,
        ramp_seconds=60.0,
        title="Downlink signal loss",
        description="RSSI collapses and frames start being dropped (packet loss).",
    ),
}


@dataclass
class FaultEffects:
    """Per-tick corrections applied on top of nominal telemetry."""

    battery_temp_add_c: float = 0.0
    battery_voltage_sag_v: float = 0.0
    solar_multiplier: float = 1.0
    signal_offset_dbm: float = 0.0
    packet_loss_prob: float = 0.0


@dataclass
class _ActiveFault:
    spec: FaultSpec
    elapsed: float = 0.0
    progress: float = 0.0
    effect: FaultEffects = field(default_factory=FaultEffects)

    def tick(self, dt: float) -> None:
        self.elapsed += dt
        self.progress = min(1.0, self.elapsed / self.spec.ramp_seconds)
        self.effect = _effects_for(self.spec.type, self.progress)


def _effects_for(fault: FaultType, p: float) -> FaultEffects:
    if fault is FaultType.BATTERY_OVERHEAT:
        # Temperature climbs ~+46 degC at full progress; pack sags up to 2.4 V.
        return FaultEffects(
            battery_temp_add_c=46.0 * p,
            battery_voltage_sag_v=2.4 * p * p,
        )
    if fault is FaultType.SOLAR_DEGRADATION:
        return FaultEffects(solar_multiplier=1.0 - 0.88 * p)
    if fault is FaultType.SIGNAL_LOSS:
        return FaultEffects(signal_offset_dbm=-34.0 * p, packet_loss_prob=0.65 * p)
    raise ValueError(f"unknown fault type: {fault}")


class FaultManager:
    """Owns the currently active faults and their combined effects."""

    def __init__(self) -> None:
        self._active: Dict[FaultType, _ActiveFault] = {}

    # -- control plane -------------------------------------------------------- #
    def activate(self, fault: FaultType) -> bool:
        """Arm (or re-arm from 0%) a fault. Returns True if it was re-armed."""
        restarted = fault in self._active
        self._active[fault] = _ActiveFault(spec=FAULT_SPECS[fault])
        return restarted

    def clear(self) -> int:
        n = len(self._active)
        self._active.clear()
        return n

    def tick(self, dt: float) -> None:
        """Advance every active fault by ``dt`` seconds of simulation time."""
        if dt <= 0.0:
            return
        for fault in self._active.values():
            fault.tick(dt)

    # -- read plane ----------------------------------------------------------- #
    @property
    def active_types(self) -> List[FaultType]:
        return list(self._active.keys())

    @property
    def active(self) -> bool:
        return bool(self._active)

    def states(self) -> List[FaultState]:
        return [
            FaultState(type=f.spec.type, progress=round(f.progress, 4))
            for f in self._active.values()
        ]

    def effects(self) -> FaultEffects:
        """Sum of all active fault effects (faults are additive)."""
        total = FaultEffects()
        for fault in self._active.values():
            e = fault.effect
            total.battery_temp_add_c += e.battery_temp_add_c
            total.battery_voltage_sag_v += e.battery_voltage_sag_v
            total.solar_multiplier *= e.solar_multiplier
            total.signal_offset_dbm += e.signal_offset_dbm
            total.packet_loss_prob += e.packet_loss_prob
        total.packet_loss_prob = min(1.0, total.packet_loss_prob)
        return total

    def progress(self, fault: FaultType) -> Optional[float]:
        active = self._active.get(fault)
        return active.progress if active else None
