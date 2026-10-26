#include "edgekit/sensirion.hpp"

#include <algorithm>

#include "edgekit/crc.hpp"

namespace edgekit::sensirion {

std::optional<std::uint16_t> read_word(const std::uint8_t* c) noexcept {
    if (c == nullptr || crc8_sensirion(c, 2) != c[2]) return std::nullopt;
    return static_cast<std::uint16_t>((c[0] << 8) | c[1]);
}

std::optional<Scd41Sample> scd41_convert(std::uint16_t co2_raw, std::uint16_t t_raw,
                                         std::uint16_t rh_raw) noexcept {
    if (co2_raw == 0) return std::nullopt;
    // SCD4x datasheet: denominator 2^16 (65536)
    return Scd41Sample{co2_raw, -45.0 + 175.0 * t_raw / 65536.0, 100.0 * rh_raw / 65536.0};
}

Sht4xSample sht4x_convert(std::uint16_t t_raw, std::uint16_t rh_raw) noexcept {
    // SHT4x datasheet: denominator 2^16 - 1 (65535) — not the same as SCD4x
    const double rh = -6.0 + 125.0 * rh_raw / 65535.0;
    return Sht4xSample{-45.0 + 175.0 * t_raw / 65535.0, std::clamp(rh, 0.0, 100.0)};
}

}  // namespace edgekit::sensirion
