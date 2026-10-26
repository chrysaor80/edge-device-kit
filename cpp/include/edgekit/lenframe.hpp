// Length-prefixed vendor RS-485 frames: [addr][cmd][len][data x len][crc_lo][crc_hi].
// Mirrors python/edgekit/lenframe.py, including the streaming decoder's resync rule.
#pragma once

#include <cstdint>
#include <optional>
#include <vector>

#include "edgekit/crc.hpp"

namespace edgekit::lenframe {

constexpr std::size_t kHeader = 3;
constexpr std::size_t kTrailer = 2;
constexpr std::size_t kMaxData = 64;

struct Frame {
    std::uint8_t addr = 0;
    std::uint8_t cmd = 0;
    Bytes data;
    bool operator==(const Frame& o) const { return addr == o.addr && cmd == o.cmd && data == o.data; }
};

Bytes build(std::uint8_t addr, std::uint8_t cmd, const Bytes& data);  // throws if data > kMaxData
std::optional<Frame> parse(const std::uint8_t* frame, std::size_t len) noexcept;

// Feed bytes as they arrive; complete CRC-valid frames come out.
class Decoder {
public:
    std::vector<Frame> feed(const std::uint8_t* chunk, std::size_t len);
    [[nodiscard]] std::size_t pending() const noexcept { return buf_.size(); }
    [[nodiscard]] std::size_t dropped_bytes() const noexcept { return dropped_; }

private:
    [[nodiscard]] std::optional<std::size_t> next_complete_frame() const;
    void drop(std::size_t n);
    Bytes buf_;
    std::size_t dropped_ = 0;
};

}  // namespace edgekit::lenframe
