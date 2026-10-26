// Modbus RTU codec (functions 0x03 and 0x06) and an in-process slave simulator.
// Mirrors python/edgekit/modbus_rtu.py; tests_cross/ checks the two agree byte for byte.
#pragma once

#include <cstdint>
#include <map>
#include <vector>

#include "edgekit/crc.hpp"

namespace edgekit::modbus {

constexpr std::uint8_t kReadHolding = 0x03;
constexpr std::uint8_t kWriteSingle = 0x06;
constexpr std::uint8_t kIllegalFunction = 0x01;
constexpr std::uint8_t kIllegalDataAddress = 0x02;
constexpr std::uint8_t kIllegalDataValue = 0x03;
constexpr std::uint16_t kMaxReadCount = 125;

enum class Status : std::uint8_t {
    Ok = 0,
    TooShort,
    BadCrc,
    WrongSlave,
    WrongFunction,
    ExceptionReply,
    BadByteCount,
    BadLength,
    EchoMismatch,
};

const char* to_string(Status s) noexcept;

struct ReadResult {
    Status status = Status::TooShort;
    std::uint8_t exception_code = 0;  // valid when status == ExceptionReply
    std::vector<std::uint16_t> registers;
    [[nodiscard]] bool ok() const noexcept { return status == Status::Ok; }
};

// Builders throw std::invalid_argument on out-of-range input (slave 1..247, count 1..125, ...).
Bytes build_read_holding(std::uint8_t slave, std::uint16_t start, std::uint16_t count);
Bytes build_write_single(std::uint8_t slave, std::uint16_t address, std::uint16_t value);

// Parsers never throw and never read outside [frame, frame+len).
ReadResult parse_read_response(const std::uint8_t* frame, std::size_t len, std::uint8_t slave,
                               std::uint16_t count);
inline ReadResult parse_read_response(const Bytes& f, std::uint8_t slave, std::uint16_t count) {
    return parse_read_response(f.data(), f.size(), slave, count);
}
Status parse_write_response(const std::uint8_t* frame, std::size_t len, std::uint8_t slave,
                            std::uint16_t address, std::uint16_t value,
                            std::uint8_t* exception_code = nullptr);

// Fake slave: silent (empty reply) on bad CRC or a different address, as real devices are.
class Slave {
public:
    explicit Slave(std::uint8_t id, std::uint32_t size = 0x10000) : id_(id), size_(size) {}
    std::map<std::uint16_t, std::uint16_t>& registers() noexcept { return regs_; }
    Bytes handle_request(const std::uint8_t* req, std::size_t len);
    Bytes handle_request(const Bytes& req) { return handle_request(req.data(), req.size()); }

private:
    [[nodiscard]] Bytes exception_reply(std::uint8_t function, std::uint8_t code) const;
    std::uint8_t id_;
    std::uint32_t size_;
    std::map<std::uint16_t, std::uint16_t> regs_;
};

}  // namespace edgekit::modbus
