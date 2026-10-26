"""Sensirion SCD41 (CO2) and SHT45 (temperature / humidity) over I2C.

The interesting part is not the conversion formulas but the failure handling:

* **No fake values.** The SCD41 answers ``read_measurement`` with zeros when no new
  sample is ready. Those zeros pass the CRC. Publishing them looks like "CO2 = 0 ppm",
  which is physically impossible and silently poisons a time series. The driver
  checks ``data_ready`` before every fresh read and refuses a zero CO2 word.
* **Self-recovery.** After a brown-out the sensor wakes up idle and needs START again,
  while the driver still believes it is running. If no fresh sample arrives for
  ``restart_after_s`` the driver forgets its started state and re-initialises.
* **Read back what was applied.** Automatic self-calibration (ASC) is disabled on every
  start, and the driver reports the value the sensor *returned*, not the value it sent.

The bus is injected (``I2cBus`` protocol), so all of this is unit-tested with a fake.
"""

from __future__ import annotations

import time
from typing import Optional, Protocol, Tuple

from .crc import crc8_sensirion


class I2cBus(Protocol):
    def write(self, addr: int, data: bytes) -> None: ...
    def write_then_read(self, addr: int, data: bytes, nbytes: int, delay_s: float = 0.0) -> bytes: ...


class SensorNotReady(RuntimeError):
    """No valid sample this cycle. The caller should skip, never substitute a value."""


def word_with_crc(value: int) -> bytes:
    raw = bytes(((value >> 8) & 0xFF, value & 0xFF))
    return raw + bytes((crc8_sensirion(raw),))


def read_word(chunk: bytes) -> int:
    """[msb, lsb, crc] -> 16-bit word. Raises ValueError on CRC mismatch."""
    if len(chunk) != 3:
        raise ValueError(f"word chunk must be 3 bytes, got {len(chunk)}")
    if crc8_sensirion(chunk[:2]) != chunk[2]:
        raise ValueError(f"CRC mismatch: {chunk.hex(' ')}")
    return (chunk[0] << 8) | chunk[1]


def read_words(raw: bytes, count: int) -> list[int]:
    if len(raw) != 3 * count:
        raise ValueError(f"expected {3 * count} bytes, got {len(raw)}")
    return [read_word(raw[i:i + 3]) for i in range(0, len(raw), 3)]


def scd41_convert(co2_raw: int, t_raw: int, rh_raw: int) -> Tuple[int, float, float]:
    """SCD4x datasheet: T = -45 + 175*raw/2^16, RH = 100*raw/2^16 (denominator 65536)."""
    return co2_raw, -45.0 + 175.0 * t_raw / 65536.0, 100.0 * rh_raw / 65536.0


def sht4x_convert(t_raw: int, rh_raw: int) -> Tuple[float, float]:
    """SHT4x datasheet: denominator 65535 (note: differs from SCD4x). RH clamped 0..100."""
    temp = -45.0 + 175.0 * t_raw / 65535.0
    rh = -6.0 + 125.0 * rh_raw / 65535.0
    return temp, min(100.0, max(0.0, rh))


class Scd41:
    ADDR = 0x62
    START_PERIODIC = bytes((0x21, 0xB1))
    STOP_PERIODIC = bytes((0x3F, 0x86))
    READ_MEASUREMENT = bytes((0xEC, 0x05))
    DATA_READY = bytes((0xE4, 0xB8))
    SET_ASC = bytes((0x24, 0x16))
    GET_ASC = bytes((0x23, 0x13))

    def __init__(self, bus: I2cBus, *, restart_after_s: float = 30.0, stop_settle_s: float = 0.5,
                 sleep=time.sleep):
        self.bus = bus
        self._restart_after_ns = int(restart_after_s * 1e9)
        self._stop_settle_s = stop_settle_s
        self._sleep = sleep
        self._started = False
        self._last_ok_ns: Optional[int] = None
        self.restarts = 0
        self.asc_enabled: Optional[bool] = None  # value read back from the sensor

    def _start(self, now_ns: int) -> None:
        # STOP first: after a process restart the sensor may still be measuring and
        # would NACK START forever.
        try:
            self.bus.write(self.ADDR, self.STOP_PERIODIC)
        except OSError:
            pass
        self._sleep(self._stop_settle_s)
        self._disable_asc()
        self.bus.write(self.ADDR, self.START_PERIODIC)
        self._started = True
        self._last_ok_ns = now_ns  # grace period until the first sample

    def _disable_asc(self) -> None:
        # ASC assumes the sensor sees 400 ppm fresh air weekly. In a closed chamber it
        # does not, and ASC drags every reading down. Must be done while idle.
        try:
            self.bus.write(self.ADDR, self.SET_ASC + word_with_crc(0))
            self._sleep(0.001)
            self.asc_enabled = bool(read_word(self.bus.write_then_read(self.ADDR, self.GET_ASC, 3, 0.001)))
        except (OSError, ValueError):
            self.asc_enabled = None  # unknown stays unknown

    def _data_ready(self) -> bool:
        word = read_word(self.bus.write_then_read(self.ADDR, self.DATA_READY, 3, 0.001))
        return (word & 0x07FF) != 0

    def read(self, now_ns: int) -> Tuple[int, float, float]:
        """Return (co2_ppm, temp_c, rh_pct) or raise SensorNotReady. Never returns a placeholder."""
        if not self._started:
            self._start(now_ns)
        if not self._data_ready():
            assert self._last_ok_ns is not None
            idle_ns = now_ns - self._last_ok_ns
            if idle_ns >= self._restart_after_ns:
                self._started = False
                self.restarts += 1
                raise SensorNotReady(f"no sample for {idle_ns / 1e9:.0f}s; re-initialising (restart #{self.restarts})")
            raise SensorNotReady("no new sample yet")
        co2_raw, t_raw, rh_raw = read_words(self.bus.write_then_read(self.ADDR, self.READ_MEASUREMENT, 9, 0.001), 3)
        if co2_raw == 0:
            raise SensorNotReady("CO2 word is 0 — physically impossible, refusing to publish")
        self._last_ok_ns = now_ns
        return scd41_convert(co2_raw, t_raw, rh_raw)


class Sht45:
    ADDR = 0x44
    MEASURE_HIGH = bytes((0xFD,))

    def __init__(self, bus: I2cBus):
        self.bus = bus

    def read(self) -> Tuple[float, float]:
        t_raw, rh_raw = read_words(self.bus.write_then_read(self.ADDR, self.MEASURE_HIGH, 6, 0.011), 2)
        return sht4x_convert(t_raw, rh_raw)
