"""Rolling buffers for statistical detection (z-score, rate-of-change).

Time is a caller-supplied float in seconds (the server passes a monotonic clock,
offline evaluation passes simulated elapsed time). Never wall-clock: that would
break fast-forwarded / test simulations.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional, Tuple


@dataclass
class Sample:
    t: float
    v: float


@dataclass
class RollingBuffer:
    """Time-bounded ring buffer with z-score and least-squares slope helpers."""

    max_sec: float = 90.0
    samples: Deque[Sample] = field(default_factory=deque)

    def add(self, t: float, v: float) -> None:
        self.samples.append(Sample(float(t), float(v)))
        self._trim(t)

    def _trim(self, now: float) -> None:
        cutoff = now - self.max_sec
        while self.samples and self.samples[0].t < cutoff:
            self.samples.popleft()

    def __len__(self) -> int:
        return len(self.samples)

    @property
    def values(self) -> List[float]:
        return [s.v for s in self.samples]

    def mean_std(self) -> Optional[Tuple[float, float]]:
        n = len(self.samples)
        if n < 2:
            return None
        vals = [s.v for s in self.samples]
        mean = sum(vals) / n
        var = sum((x - mean) ** 2 for x in vals) / (n - 1)  # unbiased
        return mean, math.sqrt(var)

    def zscore_last(self) -> Optional[float]:
        """z of the newest sample against the rest of the window."""
        if len(self.samples) < 2:
            return None
        last = self.samples[-1].v
        base = [s.v for s in list(self.samples)[:-1]]
        n = len(base)
        if n < 10:
            return None
        mean = sum(base) / n
        var = sum((x - mean) ** 2 for x in base) / (n - 1)
        std = math.sqrt(var)
        if std < 1e-9:
            if abs(last - mean) < 1e-9:
                return 0.0
            return math.inf if last > mean else -math.inf
        return (last - mean) / std

    def slope(self, window_sec: float) -> Optional[float]:
        """Least-squares slope (value units per second) over the trailing window."""
        if len(self.samples) < 3:
            return None
        cutoff = self.samples[-1].t - window_sec
        pts = [s for s in self.samples if s.t >= cutoff]
        if len(pts) < 3:
            return None
        mt = sum(p.t for p in pts) / len(pts)
        mv = sum(p.v for p in pts) / len(pts)
        num = sum((p.t - mt) * (p.v - mv) for p in pts)
        den = sum((p.t - mt) ** 2 for p in pts)
        if den <= 0:
            return 0.0
        return num / den
