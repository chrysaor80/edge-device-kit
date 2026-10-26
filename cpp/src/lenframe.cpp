#include "edgekit/lenframe.hpp"

#include <stdexcept>

namespace edgekit::lenframe {

Bytes build(std::uint8_t addr, std::uint8_t cmd, const Bytes& data) {
    if (data.size() > kMaxData) throw std::invalid_argument("data longer than 64 bytes");
    Bytes b{addr, cmd, static_cast<std::uint8_t>(data.size())};
    b.insert(b.end(), data.begin(), data.end());
    append_crc16(b);
    return b;
}

std::optional<Frame> parse(const std::uint8_t* f, std::size_t len) noexcept {
    if (f == nullptr || len < kHeader + kTrailer) return std::nullopt;
    const std::size_t dlen = f[2];
    if (dlen > kMaxData || len != kHeader + dlen + kTrailer) return std::nullopt;
    if (!check_crc16(f, len)) return std::nullopt;
    try {
        return Frame{f[0], f[1], Bytes(f + kHeader, f + kHeader + dlen)};
    } catch (...) {  // allocation failure: treat as "no frame", keep the noexcept promise
        return std::nullopt;
    }
}

void Decoder::drop(std::size_t n) {
    buf_.erase(buf_.begin(), buf_.begin() + static_cast<std::ptrdiff_t>(n));
    dropped_ += n;
}

std::optional<std::size_t> Decoder::next_complete_frame() const {
    // Offset (> 0) of the first complete CRC-valid frame further into the buffer.
    for (std::size_t k = 1; k + kHeader + kTrailer <= buf_.size(); ++k) {
        const std::size_t dlen = buf_[k + 2];
        const std::size_t end = k + kHeader + dlen + kTrailer;
        if (dlen <= kMaxData && end <= buf_.size() && check_crc16(buf_.data() + k, end - k)) return k;
    }
    return std::nullopt;
}

std::vector<Frame> Decoder::feed(const std::uint8_t* chunk, std::size_t len) {
    if (chunk != nullptr) buf_.insert(buf_.end(), chunk, chunk + len);
    std::vector<Frame> out;
    while (buf_.size() >= kHeader + kTrailer) {
        const std::size_t dlen = buf_[2];
        if (dlen > kMaxData) { drop(1); continue; }
        const std::size_t total = kHeader + dlen + kTrailer;
        if (buf_.size() < total) {
            // A garbage head may claim a length we do not have yet; do not let it
            // stall a complete frame that is already sitting behind it.
            const auto skip = next_complete_frame();
            if (!skip) break;
            drop(*skip);
            continue;
        }
        if (check_crc16(buf_.data(), total)) {
            out.push_back(Frame{buf_[0], buf_[1], Bytes(buf_.begin() + kHeader, buf_.begin() + static_cast<std::ptrdiff_t>(kHeader + dlen))});
            buf_.erase(buf_.begin(), buf_.begin() + static_cast<std::ptrdiff_t>(total));
        } else {
            drop(1);
        }
    }
    return out;
}

}  // namespace edgekit::lenframe
