// Checksums used by field devices.
//   CRC-16/MODBUS  : poly 0xA001 (reflected 0x8005), init 0xFFFF, low byte first on the wire
//   CRC-8/Sensirion: poly 0x31, init 0xFF, no reflection (catalogue name CRC-8/NRSC-5)
#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace edgekit {

using Bytes = std::vector<std::uint8_t>;

std::uint16_t crc16_modbus(const std::uint8_t* data, std::size_t len) noexcept;
inline std::uint16_t crc16_modbus(const Bytes& b) noexcept { return crc16_modbus(b.data(), b.size()); }

// Appends the CRC in wire order (low byte, high byte).
void append_crc16(Bytes& frame);

// True if the last two bytes are a valid CRC-16 of everything before them.
bool check_crc16(const std::uint8_t* frame, std::size_t len) noexcept;
inline bool check_crc16(const Bytes& b) noexcept { return check_crc16(b.data(), b.size()); }

std::uint8_t crc8_sensirion(const std::uint8_t* data, std::size_t len) noexcept;

}  // namespace edgekit
