#include "edgekit/modbus.hpp"

#include <stdexcept>

namespace edgekit::modbus {
namespace {

void put_u16(Bytes& b, std::uint16_t v) {
    b.push_back(static_cast<std::uint8_t>(v >> 8));
    b.push_back(static_cast<std::uint8_t>(v & 0xFFU));
}

std::uint16_t get_u16(const std::uint8_t* p) noexcept {
    return static_cast<std::uint16_t>((p[0] << 8) | p[1]);
}

void check_slave(std::uint8_t slave) {
    if (slave < 1 || slave > 247) throw std::invalid_argument("slave id out of range 1..247");
}

// Common checks for every reply. On ExceptionReply, *exc receives the code.
Status check_header(const std::uint8_t* f, std::size_t len, std::uint8_t slave,
                    std::uint8_t function, std::uint8_t* exc) noexcept {
    if (f == nullptr || len < 5) return Status::TooShort;
    if (!check_crc16(f, len)) return Status::BadCrc;
    if (f[0] != slave) return Status::WrongSlave;
    if (f[1] == (function | 0x80U)) {
        if (len != 5) return Status::BadLength;
        if (exc != nullptr) *exc = f[2];
        return Status::ExceptionReply;
    }
    if (f[1] != function) return Status::WrongFunction;
    return Status::Ok;
}

}  // namespace

const char* to_string(Status s) noexcept {
    switch (s) {
        case Status::Ok: return "ok";
        case Status::TooShort: return "too short";
        case Status::BadCrc: return "CRC mismatch";
        case Status::WrongSlave: return "slave mismatch";
        case Status::WrongFunction: return "unexpected function";
        case Status::ExceptionReply: return "modbus exception";
        case Status::BadByteCount: return "byte count mismatch";
        case Status::BadLength: return "frame length mismatch";
        case Status::EchoMismatch: return "echo mismatch";
    }
    return "unknown";
}

Bytes build_read_holding(std::uint8_t slave, std::uint16_t start, std::uint16_t count) {
    check_slave(slave);
    if (count < 1 || count > kMaxReadCount) throw std::invalid_argument("count out of range 1..125");
    if (static_cast<std::uint32_t>(start) + count > 0x10000U) throw std::invalid_argument("range past 0xFFFF");
    Bytes b{slave, kReadHolding};
    put_u16(b, start);
    put_u16(b, count);
    append_crc16(b);
    return b;
}

Bytes build_write_single(std::uint8_t slave, std::uint16_t address, std::uint16_t value) {
    check_slave(slave);
    Bytes b{slave, kWriteSingle};
    put_u16(b, address);
    put_u16(b, value);
    append_crc16(b);
    return b;
}

ReadResult parse_read_response(const std::uint8_t* f, std::size_t len, std::uint8_t slave,
                               std::uint16_t count) {
    ReadResult r;
    r.status = check_header(f, len, slave, kReadHolding, &r.exception_code);
    if (r.status != Status::Ok) return r;
    const std::size_t byte_count = f[2];
    if (byte_count != static_cast<std::size_t>(count) * 2U) { r.status = Status::BadByteCount; return r; }
    if (len != 3 + byte_count + 2) { r.status = Status::BadLength; return r; }
    r.registers.reserve(count);
    for (std::size_t i = 0; i < byte_count; i += 2) r.registers.push_back(get_u16(f + 3 + i));
    return r;
}

Status parse_write_response(const std::uint8_t* f, std::size_t len, std::uint8_t slave,
                            std::uint16_t address, std::uint16_t value, std::uint8_t* exc) {
    const Status s = check_header(f, len, slave, kWriteSingle, exc);
    if (s != Status::Ok) return s;
    if (len != 8) return Status::BadLength;
    if (get_u16(f + 2) != address || get_u16(f + 4) != value) return Status::EchoMismatch;
    return Status::Ok;
}

Bytes Slave::exception_reply(std::uint8_t function, std::uint8_t code) const {
    Bytes b{id_, static_cast<std::uint8_t>(function | 0x80U), code};
    append_crc16(b);
    return b;
}

Bytes Slave::handle_request(const std::uint8_t* req, std::size_t len) {
    if (req == nullptr || len != 8 || !check_crc16(req, len)) return {};
    if (req[0] != id_) return {};
    const std::uint8_t function = req[1];
    const std::uint16_t a = get_u16(req + 2);
    const std::uint16_t b = get_u16(req + 4);
    if (function == kReadHolding) {
        if (b < 1 || b > kMaxReadCount) return exception_reply(function, kIllegalDataValue);
        if (static_cast<std::uint32_t>(a) + b > size_) return exception_reply(function, kIllegalDataAddress);
        Bytes out{id_, function, static_cast<std::uint8_t>(2 * b)};
        for (std::uint32_t i = 0; i < b; ++i) {
            const auto it = regs_.find(static_cast<std::uint16_t>(a + i));
            put_u16(out, it == regs_.end() ? 0 : it->second);
        }
        append_crc16(out);
        return out;
    }
    if (function == kWriteSingle) {
        if (a >= size_) return exception_reply(function, kIllegalDataAddress);
        regs_[a] = b;
        return {req, req + len};
    }
    return exception_reply(function, kIllegalFunction);
}

}  // namespace edgekit::modbus
