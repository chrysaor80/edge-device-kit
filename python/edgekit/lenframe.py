"""Length-prefixed RS-485 frames, as used by many gas-sensor and meter vendors.

Wire format::

    [addr][cmd][len][data x len][crc_lo][crc_hi]      CRC-16/MODBUS over addr..data

This is *not* Modbus even though it reuses the Modbus CRC — mixing the two on one
bus is a common source of "works on the bench, garbage in the field" bugs.

``LenFrameDecoder`` reassembles frames from an arbitrary byte stream: partial reads,
leading garbage and corrupted frames are all expected on a shared half-duplex bus.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List

from .crc import append_crc16, check_crc16

HEADER = 3
TRAILER = 2
MAX_DATA = 64  # sanity cap: a length byte from line noise must not stall the decoder


@dataclass(frozen=True)
class LenFrame:
    addr: int
    cmd: int
    data: bytes

    def encode(self) -> bytes:
        return build(self.addr, self.cmd, self.data)


class FrameError(ValueError):
    pass


def build(addr: int, cmd: int, data: bytes = b"") -> bytes:
    if not (0 <= addr <= 0xFF and 0 <= cmd <= 0xFF):
        raise ValueError("addr/cmd must be one byte")
    if len(data) > MAX_DATA:
        raise ValueError(f"data longer than {MAX_DATA} bytes")
    return append_crc16(bytes((addr, cmd, len(data))) + data)


def parse(frame: bytes) -> LenFrame:
    """Parse exactly one complete frame. Raises FrameError on any defect."""
    if len(frame) < HEADER + TRAILER:
        raise FrameError(f"too short: {len(frame)} bytes")
    dlen = frame[2]
    if dlen > MAX_DATA:
        raise FrameError(f"length byte {dlen} exceeds {MAX_DATA}")
    if len(frame) != HEADER + dlen + TRAILER:
        raise FrameError(f"frame length {len(frame)} != {HEADER + dlen + TRAILER}")
    if not check_crc16(frame):
        raise FrameError("CRC mismatch")
    return LenFrame(frame[0], frame[1], bytes(frame[HEADER:HEADER + dlen]))


def decode_le(data: bytes, signed: bool = False) -> int:
    """Multi-byte little-endian integer (vendor value fields are usually LE)."""
    return int.from_bytes(data, "little", signed=signed)


class LenFrameDecoder:
    """Incremental decoder: ``feed()`` bytes as they arrive, get complete frames back.

    Resynchronisation rule: if the bytes at the head of the buffer cannot be a valid
    frame (bad length byte, bad CRC), drop ONE byte and try again. This recovers from
    garbage without ever skipping over a valid frame that starts one byte later.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.dropped_bytes = 0

    def feed(self, chunk: bytes) -> List[LenFrame]:
        self._buf.extend(chunk)
        return list(self._drain())

    def _drain(self) -> Iterator[LenFrame]:
        while len(self._buf) >= HEADER + TRAILER:
            dlen = self._buf[2]
            if dlen > MAX_DATA:
                self._drop_one()
                continue
            total = HEADER + dlen + TRAILER
            if len(self._buf) < total:
                # The head may be garbage whose "length byte" points past the data we
                # have. Waiting would stall every frame behind it, so first look for a
                # complete valid frame further in; only wait if there is none.
                skip = self._next_complete_frame()
                if skip is None:
                    return
                del self._buf[:skip]
                self.dropped_bytes += skip
                continue
            candidate = bytes(self._buf[:total])
            if check_crc16(candidate):
                del self._buf[:total]
                yield LenFrame(candidate[0], candidate[1], candidate[HEADER:HEADER + dlen])
            else:
                self._drop_one()

    def _next_complete_frame(self) -> int | None:
        """Offset (>0) of the first complete, CRC-valid frame in the buffer, if any."""
        buf = self._buf
        for k in range(1, len(buf) - HEADER - TRAILER + 1):
            dlen = buf[k + 2]
            end = k + HEADER + dlen + TRAILER
            if dlen <= MAX_DATA and end <= len(buf) and check_crc16(bytes(buf[k:end])):
                return k
        return None

    def _drop_one(self) -> None:
        del self._buf[0]
        self.dropped_bytes += 1

    @property
    def pending(self) -> int:
        return len(self._buf)
