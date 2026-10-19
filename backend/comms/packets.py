"""Onboard packetizer + store-and-forward buffer (SIMULATED spacecraft).

Turns each simulated telemetry reading into a CRC-framed packet, exactly the way
a real CubeSat would before handing it to the radio:

* **HOUSEKEEPING** - the regular sensor frame, appended to the onboard buffer and
  downlinked during the next ground-station pass (store-and-forward);
* **BEACON** - a small, high-priority alert emitted when an onboard limit is
  violated. Beacons jump the queue so the ground learns about a critical
  condition first.

Frame layout (big-endian, CRC-32 over every preceding byte):

    magic   "AP1"      3 bytes
    version uint8      1 byte
    type    uint8      1 byte   (0 = beacon, 1 = housekeeping)
    seq     uint32     4 bytes  (per-type sequence number)
    mission uint32     4 bytes  (simulated mission seconds)
    length  uint16     2 bytes  (UTF-8 JSON payload length)
    payload JSON       length bytes
    crc32   uint32     4 bytes  (zlib CRC-32)

All of it is simulated: no radio hardware, no real ground station.
"""

from __future__ import annotations

import json
import struct
import zlib
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Deque, Dict, List, Optional, Tuple

from models.schemas import TelemetryReading

MAGIC = b"AP1"
VERSION = 1

#: magic, version, type, seq, mission_s, length
_HEADER = struct.Struct(">3sBBIIH")
_CRC = struct.Struct(">I")
_HEADER_SIZE = _HEADER.size


class PacketType(str, Enum):
    BEACON = "beacon"
    HOUSEKEEPING = "housekeeping"


_TYPE_CODE = {PacketType.BEACON: 0, PacketType.HOUSEKEEPING: 1}
_CODE_TYPE = {code: kind for kind, code in _TYPE_CODE.items()}


class PacketError(ValueError):
    """The frame is malformed (truncated, wrong magic/version, bad JSON)."""


class CrcError(PacketError):
    """The frame arrived but its CRC-32 does not match."""


class OnboardLimitDirection(str, Enum):
    HIGH_BAD = "high_bad"
    LOW_BAD = "low_bad"


@dataclass(frozen=True)
class OnboardLimit:
    """An onboard hard limit - independent of the ground-side ML detector."""

    sensor: str
    direction: OnboardLimitDirection
    limit: float
    #: only evaluated when sun_fraction >= this (solar current in eclipse is 0)
    sunlit_min: Optional[float] = None


#: Onboard limit checks used to raise BEACON packets.
ONBOARD_LIMITS: Tuple[OnboardLimit, ...] = (
    OnboardLimit("battery_temp", OnboardLimitDirection.HIGH_BAD, 55.0),
    OnboardLimit("bus_temp", OnboardLimitDirection.HIGH_BAD, 45.0),
    OnboardLimit("gyro_rate", OnboardLimitDirection.HIGH_BAD, 0.35),
    OnboardLimit("payload_power", OnboardLimitDirection.HIGH_BAD, 58.0),
    OnboardLimit("battery_voltage", OnboardLimitDirection.LOW_BAD, 24.0),
    OnboardLimit("signal_strength", OnboardLimitDirection.LOW_BAD, -105.0),
    OnboardLimit("solar_current", OnboardLimitDirection.LOW_BAD, 3.0, sunlit_min=0.80),
)

#: Minimum simulated seconds between two beacons so a sustained fault does not
#: flood the priority queue.
BEACON_REFRACTORY_S = 30.0


@dataclass(frozen=True)
class CriticalViolation:
    sensor: str
    value: float
    limit: float
    direction: OnboardLimitDirection

    def as_payload(self) -> Dict[str, Any]:
        return {
            "sensor": self.sensor,
            "value": self.value,
            "limit": self.limit,
            "direction": self.direction.value,
            "severity": "critical",
        }


@dataclass(frozen=True)
class TelemetryPacket:
    """One CRC-framed telemetry packet."""

    seq: int
    packet_type: PacketType
    mission_s: float
    payload: Dict[str, Any]

    def encode(self) -> bytes:
        """Serialise to a CRC-framed byte string (what the radio would transmit)."""
        body = json.dumps(self.payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        header = _HEADER.pack(
            MAGIC,
            VERSION,
            _TYPE_CODE[self.packet_type],
            self.seq & 0xFFFFFFFF,
            int(round(self.mission_s)) & 0xFFFFFFFF,
            len(body),
        )
        frame = header + body
        return frame + _CRC.pack(zlib.crc32(frame) & 0xFFFFFFFF)

    @classmethod
    def decode(cls, raw: bytes) -> "TelemetryPacket":
        """Parse + CRC-verify a frame.

        Raises :class:`CrcError` when the CRC fails (corrupted in transit) and
        :class:`PacketError` for anything else malformed.
        """
        if len(raw) < _HEADER_SIZE + _CRC.size:
            raise PacketError(f"frame too short: {len(raw)} bytes")
        expected_crc = _CRC.unpack_from(raw, len(raw) - _CRC.size)[0]
        actual_crc = zlib.crc32(raw[: len(raw) - _CRC.size]) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise CrcError(f"CRC mismatch: frame says {expected_crc:#010x}, computed {actual_crc:#010x}")

        magic, version, type_code, seq, mission_s, length = _HEADER.unpack_from(raw, 0)
        if magic != MAGIC:
            raise PacketError(f"bad magic: {magic!r}")
        if version != VERSION:
            raise PacketError(f"unsupported version: {version}")
        packet_type = _CODE_TYPE.get(type_code)
        if packet_type is None:
            raise PacketError(f"unknown packet type code: {type_code}")

        body = raw[_HEADER_SIZE : _HEADER_SIZE + length]
        if len(body) != length:
            raise PacketError(f"truncated payload: want {length}, have {len(body)}")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PacketError(f"payload is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise PacketError("payload must be a JSON object")
        return cls(seq=seq, packet_type=packet_type, mission_s=float(mission_s), payload=payload)


class OnboardBuffer:
    """FIFO store-and-forward buffer. Beacons are drained before housekeeping."""

    def __init__(self, capacity: int = 8192) -> None:
        if capacity <= 0:
            raise ValueError("buffer capacity must be positive")
        self.capacity = capacity
        self._beacons: Deque[TelemetryPacket] = deque()
        self._housekeeping: Deque[TelemetryPacket] = deque()
        self.queued = 0
        self.overflow_dropped = 0

    @property
    def depth(self) -> int:
        return len(self._beacons) + len(self._housekeeping)

    @property
    def beacon_depth(self) -> int:
        return len(self._beacons)

    @property
    def housekeeping_depth(self) -> int:
        return len(self._housekeeping)

    def append(self, packet: TelemetryPacket) -> None:
        if self.depth >= self.capacity:
            # Oldest housekeeping goes first; a beacon is only dropped if the
            # buffer holds nothing else.
            self._housekeeping.popleft() if self._housekeeping else self._beacons.popleft()
            self.overflow_dropped += 1
        if packet.packet_type is PacketType.BEACON:
            self._beacons.append(packet)
        else:
            self._housekeeping.append(packet)
        self.queued += 1

    def peek_packet(self) -> Optional[TelemetryPacket]:
        """Next packet to downlink (beacons first) without removing it."""
        if self._beacons:
            return self._beacons[0]
        if self._housekeeping:
            return self._housekeeping[0]
        return None

    def pop_packet(self) -> Optional[TelemetryPacket]:
        """Pop the next packet to downlink (beacons first), or ``None`` if empty."""
        if self._beacons:
            return self._beacons.popleft()
        if self._housekeeping:
            return self._housekeeping.popleft()
        return None


class Packetizer:
    """Builds packets and decides when an onboard limit warrants a beacon."""

    def __init__(self, limits: Tuple[OnboardLimit, ...] = ONBOARD_LIMITS) -> None:
        self.limits = limits
        self._hk_seq = 0
        self._beacon_seq = 0
        self._critical: set[str] = set()
        self._last_beacon_s: Optional[float] = None

    @property
    def packets_built(self) -> int:
        return self._hk_seq + self._beacon_seq

    @staticmethod
    def check_limits(reading: TelemetryReading, limits: Tuple[OnboardLimit, ...] = ONBOARD_LIMITS) -> List[CriticalViolation]:
        """Return every onboard limit the reading violates."""
        violations: List[CriticalViolation] = []
        for spec in limits:
            if spec.sunlit_min is not None and reading.sun_fraction < spec.sunlit_min:
                continue
            value = float(getattr(reading, spec.sensor))
            breached = (
                value > spec.limit
                if spec.direction is OnboardLimitDirection.HIGH_BAD
                else value < spec.limit
            )
            if breached:
                violations.append(
                    CriticalViolation(
                        sensor=spec.sensor, value=value, limit=spec.limit, direction=spec.direction
                    )
                )
        return violations

    def packetize(self, reading: TelemetryReading, mission_s: float) -> List[TelemetryPacket]:
        """Reading -> ``[housekeeping]``, plus a beacon when a limit is newly hit."""
        packets = [
            TelemetryPacket(
                seq=self._hk_seq,
                packet_type=PacketType.HOUSEKEEPING,
                mission_s=mission_s,
                payload=reading.model_dump(mode="json"),
            )
        ]
        self._hk_seq += 1

        beacon = self._maybe_beacon(reading, mission_s)
        if beacon is not None:
            packets.append(beacon)
        return packets

    def _maybe_beacon(self, reading: TelemetryReading, mission_s: float) -> Optional[TelemetryPacket]:
        violations = self.check_limits(reading, self.limits)
        current = {v.sensor for v in violations}
        newly_critical = current - self._critical
        self._critical = current

        if not newly_critical:
            return None
        last = self._last_beacon_s
        if last is not None and mission_s - last < BEACON_REFRACTORY_S:
            return None

        first = next(v for v in violations if v.sensor in newly_critical)
        self._last_beacon_s = mission_s
        payload = first.as_payload()
        payload["ts"] = reading.timestamp.isoformat()
        beacon = TelemetryPacket(
            seq=self._beacon_seq,
            packet_type=PacketType.BEACON,
            mission_s=mission_s,
            payload=payload,
        )
        self._beacon_seq += 1
        return beacon
