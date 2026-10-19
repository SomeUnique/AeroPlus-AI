"""Simulated RF link between the spacecraft and the ground station.

Data only flows during a pass, and even then not perfectly:

* the link passes through ``NO_CONTACT -> ACQUIRING -> CONNECTED`` and back;
* throughput is capped by a data-rate limit (token bucket over the downlink bit
  rate) - a finite pass cannot drain an arbitrary backlog;
* packets are lost in transit with a probability that grows as signal quality
  drops, and a subset arrives with corrupted bytes so the ground CRC fails;
* the ``signal_loss`` fault raises the loss/corruption rates, tying the comms
  layer to the same fault model the rest of the simulation uses.

Signal quality is driven by elevation above the horizon and by the current
signal offset from :class:`services.faults.FaultManager` effects.

Everything here is SIMULATED - there is no radio, no modem and no real link.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from models.schemas import LinkState
from services.faults import FaultEffects


class TxOutcome(str, Enum):
    """What the channel did with one transmitted packet."""

    DELIVERED = "delivered"
    LOST = "lost"
    CORRUPTED = "corrupted"


@dataclass(frozen=True)
class LinkConfig:
    """SIMULATED link parameters."""

    downlink_bps: float = 57600.0
    acquiring_s: float = 8.0
    #: probability a transmitted packet is dropped outright at low signal quality
    base_packet_loss: float = 0.02
    #: probability a packet arrives with corrupted bytes (CRC will fail)
    base_crc_error: float = 0.008
    #: additional loss/corruption scaling as quality falls below 1.0
    loss_gain: float = 0.30
    crc_gain: float = 0.08


@dataclass
class LinkStats:
    packets_received: int = 0
    packets_lost: int = 0
    crc_failures: int = 0
    bytes_received: int = 0


class RfLinkModel:
    """Channel state machine, rate limiter and impairment model."""

    def __init__(self, config: LinkConfig, *, seed: Optional[int] = None) -> None:
        self.config = config
        self.rng = random.Random(seed)
        self.state = LinkState.NO_CONTACT
        self.signal_quality = 0.0
        self.stats = LinkStats()
        self.last_contact_time: Optional[datetime] = None
        self._acquiring_left = 0.0
        self._budget_bits = 0.0

    # -- state machine -------------------------------------------------------- #
    def advance(
        self,
        *,
        dt: float,
        elevation_deg: float,
        min_elevation_deg: float,
        effects: FaultEffects,
    ) -> LinkState:
        """Step the link state machine one integration step and return the new state."""
        contact = elevation_deg >= min_elevation_deg
        if not contact:
            self.state = LinkState.NO_CONTACT
            self.signal_quality = 0.0
            self._acquiring_left = 0.0
            self._budget_bits = 0.0
            return self.state

        self.signal_quality = self._quality(elevation_deg, min_elevation_deg, effects)
        if self.state is LinkState.NO_CONTACT:
            self.state = LinkState.ACQUIRING
            self._acquiring_left = self.config.acquiring_s
        elif self.state is LinkState.ACQUIRING:
            self._acquiring_left -= dt
            if self._acquiring_left <= 0.0:
                self.state = LinkState.CONNECTED

        if self.state is LinkState.CONNECTED:
            self._budget_bits += self.config.downlink_bps * dt
        return self.state

    @staticmethod
    def _quality(elevation_deg: float, min_elevation_deg: float, effects: FaultEffects) -> float:
        span = max(1.0, 90.0 - min_elevation_deg)
        geometric = 0.65 + 0.35 * max(0.0, min(1.0, (elevation_deg - min_elevation_deg) / span))
        # A downlink fault (negative offset) degrades quality; clamp to [0.05, 1].
        fault_factor = max(0.05, min(1.0, 1.0 + effects.signal_offset_dbm / 60.0))
        return max(0.0, min(1.0, geometric * fault_factor))

    # -- rate limiter --------------------------------------------------------- #
    def has_budget(self, nbytes: int) -> bool:
        return self._budget_bits >= nbytes * 8

    def spend(self, nbytes: int) -> None:
        self._budget_bits -= nbytes * 8

    @property
    def budget_bytes(self) -> int:
        return int(self._budget_bits // 8)

    # -- impairment ----------------------------------------------------------- #
    def transmit(self, raw: bytes) -> tuple[TxOutcome, bytes]:
        """Apply loss / corruption to one frame that the ground is trying to receive.

        Returns the outcome and the bytes that actually arrive (``raw`` unchanged
        when delivered, bit-flipped when corrupted, ignored when lost) so the
        ground ingest service performs a genuine CRC check on the result.
        """
        p_loss, p_corrupt = self._impairment()
        roll = self.rng.random()
        if roll < p_loss:
            self.stats.packets_lost += 1
            return TxOutcome.LOST, b""
        if roll < p_loss + p_corrupt:
            self.stats.crc_failures += 1
            return TxOutcome.CORRUPTED, corrupt_bytes(raw, self.rng)
        self.stats.packets_received += 1
        self.stats.bytes_received += len(raw)
        return TxOutcome.DELIVERED, raw

    def _impairment(self) -> tuple[float, float]:
        deficit = max(0.0, 1.0 - self.signal_quality)
        p_loss = min(0.95, self.config.base_packet_loss + self.config.loss_gain * deficit * deficit)
        p_corrupt = min(0.95, self.config.base_crc_error + self.config.crc_gain * deficit)
        # keep the combined probability sane
        if p_loss + p_corrupt > 0.98:
            scale = 0.98 / (p_loss + p_corrupt)
            p_loss *= scale
            p_corrupt *= scale
        return p_loss, p_corrupt


def corrupt_bytes(raw: bytes, rng: random.Random, *, flips: int = 2) -> bytes:
    """Flip a few bits so the frame arrives but fails its CRC check on the ground."""
    if not raw:
        return raw
    mutated = bytearray(raw)
    for _ in range(flips):
        index = rng.randrange(len(mutated))
        mutated[index] ^= 1 << rng.randrange(8)
    return bytes(mutated)
