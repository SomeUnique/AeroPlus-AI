"""Physics-informed derived series for the ML detector.

A plain Isolation Forest over raw sensor windows is weak on *correlated*
faults: a solar array output of ~0.8 A is individually in-range (eclipse does
that), and a 60 degC battery is individually plausible at high load. What is
actually abnormal is the *residual* against the expected physical relation.

This module is the single source of truth for those derived series, shared by
the offline trainer (``tools/train_iforest.py``), the streaming detector
(``detection/ml/stream.py``) and the NASA evaluation. Both train and serve
must call :func:`augment_values` so the feature definition cannot drift.

Derived series (all rebuilt each tick from the same raw reading):

* ``solar_current__resid``  - measured array current minus the illumination-
  predicted output ``SOLAR_NOMINAL_A * sun_fraction``; ~0 in health, strongly
  negative under array degradation. Backed by the same ephemeris context the
  rule/z-score layers use.
* ``battery_temp__resid``   - battery pack temperature relative to the bus
  temperature; a healthy pack tracks the bus, an over-heating pack diverges.
"""

from __future__ import annotations

from typing import Dict, Mapping

from services.telemetry import SOLAR_NOMINAL_A

#: derived series always added on top of the raw sensors before windowing
DERIVED_SERIES = ("solar_current__resid", "battery_temp__resid")


def augment_values(values: Mapping[str, float]) -> Dict[str, float]:
    """Return values plus the derived series (missing inputs are skipped)."""
    out: Dict[str, float] = dict(values)
    sun = values.get("sun_fraction")
    solar = values.get("solar_current")
    if sun is not None and solar is not None:
        out["solar_current__resid"] = float(solar) - SOLAR_NOMINAL_A * float(sun)
    battery = values.get("battery_temp")
    bus = values.get("bus_temp")
    if battery is not None and bus is not None:
        out["battery_temp__resid"] = float(battery) - float(bus)
    return out
