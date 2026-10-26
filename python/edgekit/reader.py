"""Channel reader on top of a transport — the part that must never lie.

Contract (enforced by tests/test_reader_faults.py):

* success  -> ``Reading(values=(...), ok=True)``
* any failure (timeout, CRC, partial frame, exception response, disconnect)
           -> ``Reading(values=None, ok=False, error="...")``; never hangs, never raises
* disconnect -> reset the transport so the next cycle resumes on its own

Why ``None`` and not a sentinel such as -1 or 0: a sentinel is a number. Sooner or later
some chart, average or ML feature consumes it as data. Missing must look missing.
"""

from __future__ import annotations

from typing import NamedTuple, Optional, Tuple

from .modbus_rtu import ModbusError, build_read_holding, parse_read_response
from .transport import Transport, TransportDisconnected


class Reading(NamedTuple):
    values: Optional[Tuple[float, ...]]
    ok: bool
    error: Optional[str] = None


class ModbusReader:
    def __init__(self, transport: Transport, slave_id: int = 1, start: int = 0, count: int = 2,
                 scale: float = 10.0, signed: bool = True, timeout: float = 0.5):
        self.transport = transport
        self.slave_id, self.start, self.count = slave_id, start, count
        self.scale, self.signed, self.timeout = scale, signed, timeout
        self._request = build_read_holding(slave_id, start, count)
        self.failures = 0
        self.consecutive_failures = 0

    def _fail(self, error: str) -> Reading:
        self.failures += 1
        self.consecutive_failures += 1
        return Reading(None, False, error)

    def _to_value(self, reg: int) -> float:
        if self.signed and reg & 0x8000:
            reg -= 0x10000  # temperatures below zero arrive as two's complement
        return reg / self.scale

    def read(self) -> Reading:
        try:
            response = self.transport.query(self._request, self.timeout)
        except TransportDisconnected as exc:
            try:
                self.transport.reset()
            except Exception:  # retried next cycle
                pass
            return self._fail(f"disconnected: {exc}")
        if not response:
            return self._fail("timeout")
        try:
            regs = parse_read_response(response, self.slave_id, self.count)
        except ModbusError as exc:
            return self._fail(str(exc))
        self.consecutive_failures = 0
        return Reading(tuple(self._to_value(r) for r in regs), True)
