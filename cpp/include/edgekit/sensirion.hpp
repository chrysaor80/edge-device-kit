// Sensirion SCD4x / SHT4x word decoding and unit conversion.
#pragma once

#include <cstdint>
#include <optional>

namespace edgekit::sensirion {

// [msb, lsb, crc] -> word, or nullopt on CRC mismatch.
std::optional<std::uint16_t> read_word(const std::uint8_t* chunk3) noexcept;

struct Scd41Sample {
    std::uint16_t co2_ppm;
    double temp_c;
    double rh_pct;
};

// Returns nullopt for a zero CO2 word: the sensor emits zeros when no sample is ready,
// and 0 ppm is physically impossible. A missing sample must stay missing.
std::optional<Scd41Sample> scd41_convert(std::uint16_t co2_raw, std::uint16_t t_raw,
                                         std::uint16_t rh_raw) noexcept;

struct Sht4xSample {
    double temp_c;
    double rh_pct;  // clamped to 0..100
};
Sht4xSample sht4x_convert(std::uint16_t t_raw, std::uint16_t rh_raw) noexcept;

}  // namespace edgekit::sensirion
