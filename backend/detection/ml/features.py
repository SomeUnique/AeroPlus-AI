"""Windowed feature extraction for the ML detector.

For each named series the extractor maintains a time-bounded ring window and
computes three features: rolling mean, rolling standard deviation and a
least-squares slope (value units per second). Optional *context* series
(e.g. ``sun_fraction``) contribute only their rolling mean, so the model can
learn illumination-dependent behaviour such as solar-array output without
needing a slope on a slowly varying input.

The extractor is generic over series names: the streaming detector uses it
with the seven CubeSat sensors, and the NASA SMAP/MSL evaluation uses it with
telemetry-channel columns from the real dataset.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Mapping, Optional, Sequence, Tuple


def windowed_feature_names(
    series_names: Sequence[str], context_names: Sequence[str] = ()
) -> List[str]:
    """Canonical feature order: ``<series>__{mean,std,slope}`` then ``<ctx>__mean``."""
    names: List[str] = []
    for s in series_names:
        names.extend((f"{s}__mean", f"{s}__std", f"{s}__slope"))
    for c in context_names:
        names.append(f"{c}__mean")
    return names


#: Feature names for the seven CubeSat sensors + sun_fraction context.
WINDOWED_FEATURE_NAMES = windowed_feature_names(
    (
        "battery_voltage",
        "battery_temp",
        "solar_current",
        "bus_temp",
        "signal_strength",
        "gyro_rate",
        "payload_power",
    ),
    ("sun_fraction",),
)


@dataclass
class _SeriesState:
    samples: Deque[Tuple[float, float]] = field(default_factory=deque)


def _mean_std(values: List[float]) -> Tuple[float, float]:
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return mean, 0.0
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(var)


def _slope(pairs: Deque[Tuple[float, float]]) -> float:
    """Least-squares slope (value units per second) over the window."""
    n = len(pairs)
    if n < 3:
        return 0.0
    mt = sum(p[0] for p in pairs) / n
    mv = sum(p[1] for p in pairs) / n
    num = sum((t - mt) * (v - mv) for t, v in pairs)
    den = sum((t - mt) ** 2 for t, v in pairs)
    if den <= 0:
        return 0.0
    return num / den


class WindowedFeatureExtractor:
    """Streaming windowed-feature extractor over named series.

    Time is a caller-supplied float in seconds (simulated mission time in
    both the server and the offline harness). Windows are trimmed by time, so
    missing packets shrink the window instead of desynchronising it.
    """

    def __init__(
        self,
        series_names: Sequence[str],
        context_names: Sequence[str] = (),
        *,
        window_sec: float = 20.0,
        min_samples: int = 10,
        min_span_sec: float = 8.0,
    ) -> None:
        self.series_names = tuple(series_names)
        self.context_names = tuple(context_names)
        self.window_sec = float(window_sec)
        self.min_samples = max(3, int(min_samples))
        self.min_span_sec = float(min_span_sec)
        self.feature_names: List[str] = windowed_feature_names(
            self.series_names, self.context_names
        )
        self.reset()

    def reset(self) -> None:
        self._state: Dict[str, _SeriesState] = {
            name: _SeriesState() for name in (*self.series_names, *self.context_names)
        }
        self._last_values: Dict[str, float] = {}

    @property
    def last_values(self) -> Dict[str, float]:
        """Most recent raw value per series (used for anomaly payloads)."""
        return dict(self._last_values)

    def add(self, t: float, values: Mapping[str, float]) -> None:
        t = float(t)
        for name, state in self._state.items():
            value = values.get(name)
            if value is None:
                continue
            value = float(value)
            state.samples.append((t, value))
            self._last_values[name] = value
            cutoff = t - self.window_sec
            while state.samples and state.samples[0][0] < cutoff:
                state.samples.popleft()

    def ready(self) -> bool:
        """True when every series has enough windowed data to build features."""
        for name, state in self._state.items():
            n = len(state.samples)
            if n < self.min_samples:
                return False
            span = state.samples[-1][0] - state.samples[0][0]
            if span < self.min_span_sec:
                return False
        return True

    def feature_dict(self) -> Optional[Dict[str, float]]:
        """Windowed features keyed by name, or None until :meth:`ready`."""
        if not self.ready():
            return None
        out: Dict[str, float] = {}
        for name in self.series_names:
            pairs = self._state[name].samples
            vals = [v for _, v in pairs]
            mean, std = _mean_std(vals)
            out[f"{name}__mean"] = mean
            out[f"{name}__std"] = std
            out[f"{name}__slope"] = _slope(pairs)
        for name in self.context_names:
            vals = [v for _, v in self._state[name].samples]
            mean, _ = _mean_std(vals)
            out[f"{name}__mean"] = mean
        return out

    def feature_vector(self) -> Optional[List[float]]:
        """Features ordered per :attr:`feature_names`, or None until ready."""
        fd = self.feature_dict()
        if fd is None:
            return None
        return [fd[name] for name in self.feature_names]
