"""Ground-station ingest service (SIMULATED receiver front end).

This is the boundary between "RF" and "the backend pipeline": it is deliberately
a small, transport-agnostic module so a real receiver (SatNOGS, an SDR pipeline,
a serial modem) can drop in later by calling :meth:`GroundStationIngest.receive`
with raw frame bytes.

Responsibilities:

* verify the CRC-32 and decode the frame (corrupted frames are dropped + counted);
* reorder housekeeping packets by sequence number (beacons are prioritised on
  the spacecraft, so they arrive out of order and bypass the reorder buffer);
* forward decoded packets **in sequence order** to a sink callback.

All data is SIMULATED.
"""

from __future__ import annotations

from collections import OrderedDict
from enum import Enum
from typing import Callable, Dict, Optional

from comms.packets import CrcError, PacketError, PacketType, TelemetryPacket

#: A real receiver would hand verified packets to whatever consumes them.
PacketSink = Callable[[TelemetryPacket], None]


class IngestOutcome(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    BUFFERED = "buffered"        # arrived ahead of a gap, held for reordering
    CRC_FAILED = "crc_failed"
    MALFORMED = "malformed"


class GroundStationIngest:
    """CRC-verify -> decode -> reorder -> forward."""

    def __init__(self, sink: PacketSink, *, reorder_window: int = 16) -> None:
        self._sink = sink
        self.reorder_window = max(1, reorder_window)
        # ground-side counters
        self.received = 0
        self.crc_failed = 0
        self.malformed = 0
        self.forwarded = 0
        self.duplicates = 0
        self.reordered = 0
        self.gap_flushes = 0
        self._next_expected: Optional[int] = None
        self._pending: "OrderedDict[int, TelemetryPacket]" = OrderedDict()

    def reset(self) -> None:
        self.received = 0
        self.crc_failed = 0
        self.malformed = 0
        self.forwarded = 0
        self.duplicates = 0
        self.reordered = 0
        self.gap_flushes = 0
        self._next_expected = None
        self._pending.clear()

    def receive(self, raw: bytes) -> IngestOutcome:
        """Ingest one received frame. Returns what happened to it."""
        try:
            packet = TelemetryPacket.decode(raw)
        except CrcError:
            self.crc_failed += 1
            return IngestOutcome.CRC_FAILED
        except PacketError:
            self.malformed += 1
            return IngestOutcome.MALFORMED

        self.received += 1

        # Beacons are priority traffic on the spacecraft and are handled out of
        # band - forward immediately, no reordering, no gap bookkeeping.
        if packet.packet_type is PacketType.BEACON:
            self._sink(packet)
            self.forwarded += 1
            return IngestOutcome.ACCEPTED

        if self._next_expected is None:
            self._next_expected = packet.seq

        if packet.seq < self._next_expected or packet.seq in self._pending:
            self.duplicates += 1
            return IngestOutcome.DUPLICATE

        if packet.seq == self._next_expected:
            self._forward(packet)
            self._next_expected += 1
            self._drain_pending()
            return IngestOutcome.ACCEPTED

        # A gap: hold it so a late-arriving earlier packet can be emitted in
        # order. The hold is bounded - once the window fills we assume the
        # missing packet was lost and flush what we have, in sequence order.
        self.reordered += 1
        self._pending[packet.seq] = packet
        if len(self._pending) >= self.reorder_window:
            self._flush_gap()
        return IngestOutcome.BUFFERED

    # -- internals ------------------------------------------------------------ #
    def _forward(self, packet: TelemetryPacket) -> None:
        self._sink(packet)
        self.forwarded += 1

    def _drain_pending(self) -> None:
        while True:
            nxt = self._pending.pop(self._next_expected, None)
            if nxt is None:
                return
            self._forward(nxt)
            self._next_expected += 1

    def _flush_gap(self) -> None:
        """A packet we were waiting for is presumed lost: emit the rest in order."""
        self.gap_flushes += 1
        for seq in sorted(self._pending):
            packet = self._pending.pop(seq)
            self._next_expected = seq + 1
            self._forward(packet)

    def stats(self) -> Dict[str, int]:
        return {
            "received": self.received,
            "crc_failed": self.crc_failed,
            "malformed": self.malformed,
            "forwarded": self.forwarded,
            "duplicates": self.duplicates,
            "reordered": self.reordered,
            "gap_flushes": self.gap_flushes,
            "pending": len(self._pending),
        }
