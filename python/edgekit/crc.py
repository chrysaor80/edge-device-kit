"""Checksums used by field devices. Pure functions, no hardware needed.

* CRC-16/MODBUS  — poly 0xA001 (reflected 0x8005), init 0xFFFF, sent low byte first.
  Used by Modbus RTU and by many vendor RS-485 protocols that borrow its framing.
* CRC-8/Sensirion — poly 0x31, init 0xFF, no reflection, xor-out 0x00.
  Guards every 16-bit word on SCD4x / SHT4x I2C sensors.
"""

from __future__ import annotations


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def append_crc16(body: bytes) -> bytes:
    """Return ``body`` followed by its CRC-16 in wire order (low byte, high byte)."""
    crc = crc16_modbus(body)
    return body + bytes((crc & 0xFF, crc >> 8))


def check_crc16(frame: bytes) -> bool:
    """True if the last two bytes of ``frame`` are a valid CRC-16 of the rest."""
    if len(frame) < 3:
        return False
    return crc16_modbus(frame[:-2]) == (frame[-2] | (frame[-1] << 8))


def crc8_sensirion(data: bytes) -> int:
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc
