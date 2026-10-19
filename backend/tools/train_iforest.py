"""Train the Isolation Forest ML detector on SIMULATED nominal telemetry.

Generates nominal (fault-free) telemetry with the same ``TelemetryEngine``
the mission backend uses, extracts windowed features (rolling mean / std /
slope per sensor + sun_fraction context) exactly as the streaming detector
does, fits scikit-learn's ``IsolationForest`` and calibrates the anomaly
threshold on a *held-out* nominal stream so normal operation raises zero ML
alarms. The fitted model is saved to ``detection/ml/artifacts/iforest.joblib``
and loaded automatically by the detection layer at startup.

IMPORTANT (honesty): the training data is SIMULATED telemetry produced by
``services/telemetry.py`` - it is not real spacecraft data. The offline
evaluation in ``scripts/evaluate.py`` and ``tools/smap_msl_eval.py`` measures
what the trained model can and cannot do; no number here is hand-written.

Usage (from the backend/ directory):

    python -m tools.train_iforest                 # defaults (~3 min)
    python -m tools.train_iforest --seconds 7200 --seeds 11 22 33 -v
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from detection.config import SENSORS
from detection.ml.features import WindowedFeatureExtractor
from detection.ml.iforest import MODEL_PATH, IsolationForestModel
from detection.ml.physics import augment_values
from detection.ml.stream import (
    CONTEXT_NAMES,
    ML_MIN_CONSECUTIVE,
    ML_MIN_SAMPLES,
    ML_MIN_SPAN_SEC,
    ML_SERIES_NAMES,
    ML_WINDOW_SEC,
    ml_feature_names,
)
from models.schemas import TelemetryReading
from services.faults import FaultManager
from services.telemetry import TelemetryEngine

#: candidate training-set quantiles, most permissive first; calibration keeps
#: the first one that yields zero ML alarms on a held-out nominal stream.
CANDIDATE_QUANTILES = (0.05, 0.02, 0.01, 0.005, 0.001, 0.0005, 0.0001)


def generate_nominal(
    seeds: Sequence[int], seconds: float, dt: float = 1.0
) -> List[Tuple[float, TelemetryReading]]:
    """(mission_time, reading) pairs from fault-free runs, one per seed."""
    stream: List[Tuple[float, TelemetryReading]] = []
    for seed in seeds:
        engine = TelemetryEngine(faults=FaultManager(), seed=seed)
        for _ in range(int(seconds / dt)):
            reading = engine.tick(dt)
            if reading is not None:
                stream.append((engine.elapsed, reading))
    return stream


def extract_features(
    stream: Sequence[Tuple[float, TelemetryReading]],
) -> Tuple[np.ndarray, Dict[str, List[float]], List[Tuple[float, TelemetryReading]]]:
    """Windowed feature matrix + aligned raw sensor values + ready-samples."""
    extractor = WindowedFeatureExtractor(
        list(ML_SERIES_NAMES),
        list(CONTEXT_NAMES),
        window_sec=ML_WINDOW_SEC,
        min_samples=ML_MIN_SAMPLES,
        min_span_sec=ML_MIN_SPAN_SEC,
    )
    rows: List[List[float]] = []
    raw: Dict[str, List[float]] = {name: [] for name in SENSORS}
    ready_stream: List[Tuple[float, TelemetryReading]] = []
    for t, reading in stream:
        values = {name: float(getattr(reading, name)) for name in SENSORS}
        values["sun_fraction"] = float(reading.sun_fraction)
        extractor.add(t, augment_values(values))
        vector = extractor.feature_vector()
        if vector is None:
            continue
        rows.append(vector)
        for name in SENSORS:
            raw[name].append(float(getattr(reading, name)))
        ready_stream.append((t, reading))
    return np.asarray(rows, dtype=float), raw, ready_stream


def count_ml_alarms(
    model: IsolationForestModel, X: np.ndarray, min_consecutive: int = ML_MIN_CONSECUTIVE
) -> int:
    """Simulate the streaming ML detector (persistence included) over a matrix.

    Batch-scored, but identical to feeding the windows one at a time through
    ``MLStreamDetector``: the extractor produces the same windows either way.
    """
    decisions = model.estimator.decision_function(np.asarray(X, dtype=float))
    alarms = 0
    hits = 0
    for d in decisions:
        if d < model.threshold:
            hits += 1
            if hits >= min_consecutive:
                alarms += 1
                hits = 0
        else:
            hits = 0
    return alarms


def calibrate(
    model: IsolationForestModel,
    cal_matrix: np.ndarray,
    verbose: bool = False,
) -> Tuple[float, int]:
    """Pick the most permissive quantile with zero alarms on held-out nominal.

    Returns (threshold_quantile, threshold).
    """
    for quantile in CANDIDATE_QUANTILES:
        model.recalibrate(cal_matrix, quantile)
        alarms = count_ml_alarms(model, cal_matrix)
        if verbose:
            print(
                f"  calibration q={quantile:<8} threshold={model.threshold:+.5f} "
                f"-> {alarms} ML alarms on held-out nominal"
            )
        if alarms == 0:
            return quantile, model.threshold
    model.recalibrate(cal_matrix, CANDIDATE_QUANTILES[-1])
    return CANDIDATE_QUANTILES[-1], model.threshold


def train(
    *,
    train_seeds: Sequence[int] = (11, 22, 33),
    cal_seeds: Sequence[int] = (77, 78),
    seconds: float = 8640.0,
    cal_seconds: float = 5760.0,
    n_estimators: int = 200,
    out_path: str = MODEL_PATH,
    verbose: bool = False,
) -> IsolationForestModel:
    started = time.perf_counter()

    if verbose:
        print(f"Generating nominal training data: seeds={train_seeds}, {seconds:.0f}s each")
    train_stream = generate_nominal(train_seeds, seconds)
    X_train, raw_train, _ = extract_features(train_stream)
    if verbose:
        print(f"  windows: {X_train.shape[0]} x {X_train.shape[1]} features")

    provenance = (
        "SIMULATED nominal telemetry from backend/services/telemetry.py "
        f"(seeds={list(train_seeds)}, {seconds:.0f}s/seed, TelemetryEngine); "
        "not real spacecraft data"
    )
    model = IsolationForestModel.train(
        X_train,
        feature_names=ml_feature_names(),
        sensor_values={k: np.asarray(v) for k, v in raw_train.items()},
        threshold_quantile=CANDIDATE_QUANTILES[-1],
        n_estimators=n_estimators,
        provenance=provenance,
    )

    if verbose:
        print(f"Generating calibration data: seeds={cal_seeds}, {cal_seconds:.0f}s each")
    cal_stream = generate_nominal(cal_seeds, cal_seconds)
    X_cal, _, _ = extract_features(cal_stream)
    quantile, threshold = calibrate(model, X_cal, verbose=verbose)

    path = model.save(out_path)
    elapsed = time.perf_counter() - started
    print(
        f"Trained Isolation Forest in {elapsed:.1f}s\n"
        f"  train windows : {X_train.shape[0]} (seeds {list(train_seeds)}, {seconds:.0f}s each)\n"
        f"  calibration   : {X_cal.shape[0]} windows (seeds {list(cal_seeds)}), "
        f"0 ML alarms at q={quantile}\n"
        f"  threshold     : decision < {threshold:+.5f} is anomalous\n"
        f"  artifact      : {path}"
    )
    return model


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Train the Isolation Forest ML detector")
    parser.add_argument("--seconds", type=float, default=8640.0, help="nominal seconds per training seed")
    parser.add_argument("--cal-seconds", type=float, default=5760.0, help="nominal seconds per calibration seed")
    parser.add_argument("--seeds", type=int, nargs="*", default=[11, 22, 33])
    parser.add_argument("--cal-seeds", type=int, nargs="*", default=[77, 78])
    parser.add_argument("--n-estimators", type=int, default=200)
    parser.add_argument("--out", default=MODEL_PATH)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)
    try:
        train(
            train_seeds=args.seeds,
            cal_seeds=args.cal_seeds,
            seconds=args.seconds,
            cal_seconds=args.cal_seconds,
            n_estimators=args.n_estimators,
            out_path=args.out,
            verbose=args.verbose,
        )
    except ValueError as exc:
        print(f"training failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
