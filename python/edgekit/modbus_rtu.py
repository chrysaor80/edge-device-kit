"""Modbus RTU codec (function 0x03 / 0x06) and an in-process slave simulator.

Written from the public Modbus specification. The parser is deliberately strict:
every check below exists because a real device or cable produced that failure,
and a lenient parser turns those failures into wrong numbers instead of errors.
"""

from __future__ import annotations

from typing import Dict, List

from .crc import append_crc16, check_crc16

READ_HOLDING = 0x03
WRITE_SINGLE = 0x06

# Exception codes from the specification
ILLEGAL_FUNCTION = 0x01
ILLEGAL_DATA_ADDRESS = 0x02
ILLEGAL_DATA_VALUE = 0x03

MAX_READ_COUNT = 125  # spec limit for 0x03 (250 data bytes)


class ModbusError(Exception):
    """Frame validation failure or a Modbus exception response."""

    def __init__(self, message: str, exception_code: int | None = None):
        super().__init__(message)
        self.exception_code = exception_code


def _u16(value: int) -> bytes:
    return bytes(((value >> 8) & 0xFF, value & 0xFF))


def build_read_holding(slave: int, start: int, count: int) -> bytes:
    if not 1 <= slave <= 247:
        raise ValueError(f"slave id out of range: {slave}")
    if not 1 <= count <= MAX_READ_COUNT:
        raise ValueError(f"register count out of range: {count}")
    if not 0 <= start <= 0xFFFF or start + count > 0x10000:
        raise ValueError(f"register range out of bounds: {start}+{count}")
    return append_crc16(bytes((slave, READ_HOLDING)) + _u16(start) + _u16(count))


def build_write_single(slave: int, address: int, value: int) -> bytes:
    if not 1 <= slave <= 247:
        raise ValueError(f"slave id out of range: {slave}")
    if not 0 <= address <= 0xFFFF or not 0 <= value <= 0xFFFF:
        raise ValueError("address/value must fit in 16 bits")
    return append_crc16(bytes((slave, WRITE_SINGLE)) + _u16(address) + _u16(value))


def _check_header(frame: bytes, slave: int, function: int) -> None:
    if len(frame) < 5:
        raise ModbusError(f"too short: {len(frame)} bytes")
    if not check_crc16(frame):
        raise ModbusError("CRC mismatch")
    if frame[0] != slave:
        raise ModbusError(f"slave mismatch: {frame[0]} != {slave}")
    if frame[1] == (function | 0x80):
        if len(frame) != 5:
            raise ModbusError(f"exception frame length {len(frame)} != 5")
        raise ModbusError(f"modbus exception {frame[2]}", exception_code=frame[2])
    if frame[1] != function:
        raise ModbusError(f"unexpected function 0x{frame[1]:02X}")


def parse_read_response(frame: bytes, slave: int, count: int) -> List[int]:
    """Validate a 0x03 response and return the register values."""
    _check_header(frame, slave, READ_HOLDING)
    byte_count = frame[2]
    if byte_count != 2 * count:
        raise ModbusError(f"byte count {byte_count} != {2 * count}")
    if len(frame) != 3 + byte_count + 2:
        raise ModbusError(f"frame length {len(frame)} != {3 + byte_count + 2}")
    data = frame[3:-2]
    return [(data[i] << 8) | data[i + 1] for i in range(0, byte_count, 2)]


def parse_write_response(frame: bytes, slave: int, address: int, value: int) -> None:
    """A 0x06 response must echo the request exactly."""
    _check_header(frame, slave, WRITE_SINGLE)
    if len(frame) != 8:
        raise ModbusError(f"frame length {len(frame)} != 8")
    echo_addr = (frame[2] << 8) | frame[3]
    echo_val = (frame[4] << 8) | frame[5]
    if (echo_addr, echo_val) != (address, value):
        raise ModbusError(f"echo mismatch: {echo_addr}={echo_val}, sent {address}={value}")


class ModbusRtuSlave:
    """Fake slave device: request bytes in, response bytes out.

    Silent (b'') on bad CRC or another slave's address, as real devices are.
    """

    def __init__(self, slave_id: int = 1, registers: Dict[int, int] | None = None,
                 size: int = 0x10000):
        self.slave_id = slave_id
        self.size = size
        self.registers: Dict[int, int] = dict(registers or {})

    def _exception(self, function: int, code: int) -> bytes:
        return append_crc16(bytes((self.slave_id, function | 0x80, code)))

    def handle_request(self, request: bytes) -> bytes:
        if len(request) != 8 or not check_crc16(request):
            return b""
        slave, function = request[0], request[1]
        if slave != self.slave_id:
            return b""
        a = (request[2] << 8) | request[3]
        b = (request[4] << 8) | request[5]
        if function == READ_HOLDING:
            if not 1 <= b <= MAX_READ_COUNT:
                return self._exception(function, ILLEGAL_DATA_VALUE)
            if a + b > self.size:
                return self._exception(function, ILLEGAL_DATA_ADDRESS)
            data = b"".join(_u16(self.registers.get(a + i, 0) & 0xFFFF) for i in range(b))
            return append_crc16(bytes((slave, function, len(data))) + data)
        if function == WRITE_SINGLE:
            if a >= self.size:
                return self._exception(function, ILLEGAL_DATA_ADDRESS)
            self.registers[a] = b
            return request
        return self._exception(function, ILLEGAL_FUNCTION)
