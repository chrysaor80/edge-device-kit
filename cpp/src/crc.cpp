#include "edgekit/crc.hpp"

namespace edgekit {

std::uint16_t crc16_modbus(const std::uint8_t* data, std::size_t len) noexcept {
    std::uint16_t crc = 0xFFFF;
    for (std::size_t i = 0; i < len; ++i) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; ++bit) {
            crc = ((crc & 1U) != 0U) ? static_cast<std::uint16_t>((crc >> 1) ^ 0xA001U)
                             : static_cast<std::uint16_t>(crc >> 1);
        }
    }
    return crc;
}

void append_crc16(Bytes& frame) {
    const std::uint16_t crc = crc16_modbus(frame);
    frame.push_back(static_cast<std::uint8_t>(crc & 0xFFU));
    frame.push_back(static_cast<std::uint8_t>(crc >> 8));
}

bool check_crc16(const std::uint8_t* frame, std::size_t len) noexcept {
    if (len < 3) return false;
    const auto got = static_cast<std::uint16_t>(frame[len - 2] | (static_cast<unsigned>(frame[len - 1]) << 8U));
    return crc16_modbus(frame, len - 2) == got;
}

std::uint8_t crc8_sensirion(const std::uint8_t* data, std::size_t len) noexcept {
    std::uint8_t crc = 0xFF;
    for (std::size_t i = 0; i < len; ++i) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; ++bit) {
            const unsigned shifted = static_cast<unsigned>(crc) << 1U;
            crc = static_cast<std::uint8_t>(((crc & 0x80U) != 0U) ? (shifted ^ 0x31U) : shifted);
        }
    }
    return crc;
}

}  // namespace edgekit
