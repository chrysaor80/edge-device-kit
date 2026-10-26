"""Request/response transports: in-process loopback (tests) and real serial (field).

The same reader and the same test suite run against both — only the transport changes.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod

from .faults import Fault, FaultSchedule
from .modbus_rtu import ModbusRtuSlave

try:  # termios.error does NOT inherit from OSError (observed on Raspberry Pi OS, pyserial 3.5)
    import termios
    SERIAL_IO_ERRORS: tuple = (OSError, termios.error)
except ImportError:  # Windows
    SERIAL_IO_ERRORS = (OSError,)


class TransportDisconnected(Exception):
    """The link is gone (port closed, USB adapter re-enumerated, device asleep)."""


class Transport(ABC):
    @abstractmethod
    def query(self, request: bytes, timeout: float) -> bytes:
        """Send a request, return the raw response. b'' on timeout. Raises TransportDisconnected."""

    def reset(self) -> None:
        """Re-establish the link after a disconnect."""


class LoopbackTransport(Transport):
    """Drives a simulated slave in-process and applies a fault schedule. No real sleeping."""

    def __init__(self, slave: ModbusRtuSlave, schedule: FaultSchedule | None = None):
        self.slave = slave
        self.schedule = schedule or FaultSchedule()
        self.calls = 0
        self._connected = True

    def query(self, request: bytes, timeout: float) -> bytes:
        fault: Fault = self.schedule.at(self.calls)
        self.calls += 1
        if fault.disconnect:
            self._connected = False
        if not self._connected:
            raise TransportDisconnected("loopback disconnected")
        response = self.slave.handle_request(request)
        if fault.drop or fault.delay_s > timeout:
            return b""
        return fault.mangle(response)

    def reset(self) -> None:
        self._connected = True


class SerialTransport(Transport):
    """Modbus RTU over a real serial port (pyserial).

    * Frames are delimited by line silence (3.5 character times), as the RTU spec requires,
      so the transport does not need to know the expected response length.
    * Lazy open: constructing the object never touches hardware.
    * Dead handle recovery: when a USB-RS485 adapter re-enumerates, the old file
      descriptor returns EIO forever. The handle is dropped and reopened once in the
      same call; the device path is expected to be a stable /dev/serial/by-path link.
    """

    def __init__(self, port: str, baud: int = 9600, parity: str = "N", stopbits: int = 1):
        self.port, self.baud, self.parity, self.stopbits = port, baud, parity, stopbits
        self._ser = None
        self._lock = threading.Lock()
        bits_per_char = 1 + 8 + (0 if parity == "N" else 1) + stopbits
        self.char_time_s = bits_per_char / baud
        # spec: fixed 1.75 ms above 19200 baud
        self.frame_gap_s = 0.00175 if baud > 19200 else 3.5 * self.char_time_s

    def _open(self):
        if self._ser is None:
            import serial
            self._ser = serial.Serial(self.port, self.baud, bytesize=8, parity=self.parity,
                                      stopbits=self.stopbits, timeout=0)
        return self._ser

    def _drop_handle(self) -> None:
        try:
            if self._ser is not None:
                self._ser.close()
        except Exception:  # closing a dead handle may fail; dropping it is the point
            pass
        self._ser = None

    def _exchange(self, request: bytes, timeout: float) -> bytes:
        ser = self._open()
        ser.reset_input_buffer()
        ser.write(request)
        ser.flush()
        deadline = time.monotonic() + timeout
        buf = bytearray()
        last_rx = None
        while True:
            chunk = ser.read(ser.in_waiting or 1)
            now = time.monotonic()
            if chunk:
                buf.extend(chunk)
                last_rx = now
            elif last_rx is not None and now - last_rx >= self.frame_gap_s:
                return bytes(buf)  # silence after data = end of frame
            elif last_rx is None and now >= deadline:
                return b""
            if len(buf) > 256:  # no RTU frame is this long; stop reading noise
                return bytes(buf)
            time.sleep(self.char_time_s)

    def query(self, request: bytes, timeout: float) -> bytes:
        with self._lock:
            for attempt in (0, 1):
                try:
                    return self._exchange(request, timeout)
                except SERIAL_IO_ERRORS as exc:
                    self._drop_handle()
                    if attempt:
                        raise TransportDisconnected(str(exc)) from exc
        return b""  # unreachable

    def reset(self) -> None:
        with self._lock:
            self._drop_handle()

    def close(self) -> None:
        self.reset()
