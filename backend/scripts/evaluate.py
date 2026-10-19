"""Detection evaluation harness (offline, no server).

Simulates the telemetry engine + detection service and reports:

  1. nominal run  - false-alarm count over a configurable duration;
  2. fault run    - detection latency for each injected fault.

Solar degradation is only observable while the array is illuminated, so its
budget covers a full orbit and latency is measured from the first sunlit sample
after injection (an eclipse is not a detector failure).

Usage (from the backend/ directory):

    python -m tools.detection_eval                 # defaults
    python -m tools.detection_eval --nominal 1200 --seed 7
    python -m tools.detection_eval --faults solar_degradation signal_loss

Exit code is non-zero if the nominal run raises any anomaly or a fault is never
detected within its budget, so it can gate CI.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from detection import DetectionService
from models.schemas import AnomalyMessage, FaultType
from services.faults import FAULT_SPECS, FaultManager
from services.telemetry import TelemetryEngine

SUNLIT_FRACTION = 0.80


@dataclass(frozen=True)
class Expectation:
    subsystem: str
    budget_s: float
    requires_sunlight: bool = False


FAULT_EXPECTATIONS: Dict[FaultType, Expectation] = {
    FaultType.BATTERY_OVERHEAT: Expectation("Thermal", 150.0),
    FaultType.SOLAR_DEGRADATION: Expectation("Power", 6000.0, requires_sunlight=True),
    FaultType.SIGNAL_LOSS: Expectation("Comms", 150.0),
}


def _drain(engine: TelemetryEngine, svc: DetectionService, dt: float) -> List[AnomalyMessage]:
    reading = engine.tick(dt)
    if reading is None:
        return []
    return [p for p in svc.process(reading, now=engine.elapsed) if isinstance(p, AnomalyMessage)]


def run_nominal(seconds: float, seed: Optional[int], dt: float = 1.0, verbose: bool = False) -> int:
    engine = TelemetryEngine(faults=FaultManager(), seed=seed)
    svc = DetectionService()
    for _ in range(int(seconds / dt)):
        for a in _drain(engine, svc, dt):
            if verbose:
                print(f"  [FA] t={engine.elapsed:7.1f}s {a.subsystem:<8} {a.sensor.value:<16} "
                      f"{a.detector_type.value:<7} {a.severity.value:<8} {a.description}")
    statuses = svc.status_snapshot()
    print(f"Nominal run: {int(seconds)} s @ {1/dt:.0f} Hz, seed={seed}")
    print(f"  anomalies raised : {svc.anomalies_raised}")
    print(f"  subsystem status : {statuses}")
    print(f"  FALSE ALARMS     : {svc.anomalies_raised}  {'OK' if svc.anomalies_raised == 0 else 'FAIL'}")
    return svc.anomalies_raised


def run_fault(fault: FaultType, warmup_s: float, seed: Optional[int], dt: float = 1.0,
              verbose: bool = False) -> Tuple[Optional[float], Optional[AnomalyMessage], bool]:
    engine = TelemetryEngine(faults=FaultManager(), seed=seed)
    svc = DetectionService()
    for _ in range(int(warmup_s / dt)):
        _drain(engine, svc, dt)

    engine.faults.activate(fault)
    exp = FAULT_EXPECTATIONS[fault]
    spec = FAULT_SPECS[fault]

    detected_at: Optional[float] = None
    first: Optional[AnomalyMessage] = None
    sunlit_since: Optional[float] = None

    for _ in range(int(exp.budget_s / dt)):
        reading = engine.tick(dt)
        if reading is None:
            continue
        elapsed = engine.elapsed - warmup_s
        if sunlit_since is None and reading.sun_fraction >= SUNLIT_FRACTION:
            sunlit_since = elapsed
        for a in svc.process(reading, now=engine.elapsed):
            if not isinstance(a, AnomalyMessage):
                continue
            if verbose:
                print(f"  t={elapsed:6.1f}s {a.subsystem:<8} {a.sensor.value:<16} "
                      f"{a.detector_type.value:<7} {a.severity.value:<8} {a.description}")
            if a.subsystem == exp.subsystem and first is None:
                detected_at, first = elapsed, a
        if detected_at is not None:
            break

    if exp.requires_sunlight and detected_at is not None:
        reference = sunlit_since if sunlit_since is not None else 0.0
        ok = detected_at <= reference + spec.ramp_seconds + 30.0
    else:
        ok = detected_at is not None and detected_at <= spec.ramp_seconds + 30.0

    lat = f"{detected_at:.0f}s" if detected_at is not None else f">{exp.budget_s:.0f}s (MISS)"
    via = f"{first.sensor.value}/{first.detector_type.value}" if first else "-"
    status = svc.status_snapshot().get(exp.subsystem, "?")
    print(f"Fault {fault.value:<18} ramp={spec.ramp_seconds:5.0f}s -> {via:<28} "
          f"latency={lat:<12} {exp.subsystem}={status:<8} [{'OK' if ok else 'FAIL'}]")
    return detected_at, first, ok


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Detection evaluation harness")
    parser.add_argument("--nominal", type=float, default=3600.0, help="nominal seconds (default 3600)")
    parser.add_argument("--warmup", type=float, default=300.0, help="fault-run warmup seconds")
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--faults", nargs="*", default=[f.value for f in FAULT_EXPECTATIONS],
                        choices=[f.value for f in FAULT_EXPECTATIONS])
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    print("=" * 78)
    nominal_alarms = run_nominal(args.nominal, args.seed, verbose=args.verbose)
    print("=" * 78)
    failures = 0
    for name in args.faults:
        _, _, ok = run_fault(FaultType(name), args.warmup, args.seed, verbose=args.verbose)
        if not ok:
            failures += 1
    print("=" * 78)
    print(f"RESULT: nominal_false_alarms={nominal_alarms} fault_failures={failures}")
    return 1 if (nominal_alarms or failures) else 0


if __name__ == "__main__":
    sys.exit(main())
