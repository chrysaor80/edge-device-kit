"""Deterministic fault injection for transport tests.

Testing only the happy path is how serial code passes CI and then fails on the first
loose connector. Each ``Fault`` models one real failure mode; a ``FaultSchedule``
applies them to specific calls so tests are reproducible (no randomness).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class Fault:
    drop: bool = False              # no answer -> timeout
    delay_s: float = 0.0            # answer arrives late (logical clock)
    truncate_to: int | None = None  # partial frame
    corrupt_crc: bool = False       # last byte flipped
    garbage_prefix: bytes = b""     # line noise before the frame
    disconnect: bool = False        # USB adapter vanished / port closed

    def mangle(self, frame: bytes) -> bytes:
        out = self.garbage_prefix + frame
        if self.corrupt_crc and out:
            out = out[:-1] + bytes((out[-1] ^ 0xFF,))
        if self.truncate_to is not None:
            out = out[: self.truncate_to]
        return out


NORMAL = Fault()
DROPOUT = Fault(drop=True)
DISCONNECT = Fault(disconnect=True)
BAD_CRC = Fault(corrupt_crc=True)


def partial(n: int = 4) -> Fault:
    return Fault(truncate_to=n)


def garbage(prefix: bytes = b"\xAB\xCD") -> Fault:
    return Fault(garbage_prefix=prefix)


def latency(seconds: float) -> Fault:
    return Fault(delay_s=seconds)


@dataclass
class FaultSchedule:
    """Fault for the N-th call; calls past the end of the list are fault-free."""

    faults: List[Fault] = field(default_factory=list)

    def at(self, call_index: int) -> Fault:
        if 0 <= call_index < len(self.faults):
            return self.faults[call_index]
        return NORMAL
