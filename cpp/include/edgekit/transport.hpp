// Request/response transports and the Modbus channel reader.
#pragma once

#include <chrono>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

#include "edgekit/modbus.hpp"

namespace edgekit {

class Disconnected : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class Transport {
public:
    virtual ~Transport() = default;
    // Empty reply = timeout. Throws Disconnected when the link is gone.
    virtual Bytes query(const Bytes& request, std::chrono::milliseconds timeout) = 0;
    virtual void reset() {}
};

// POSIX serial port, Modbus RTU framing by line silence (3.5 character times).
// Opens lazily; on an I/O error (USB adapter re-enumerated) drops the handle and
// retries once in the same call, then throws Disconnected.
class SerialTransport final : public Transport {
public:
    explicit SerialTransport(std::string path, int baud = 9600);
    ~SerialTransport() override;
    SerialTransport(const SerialTransport&) = delete;
    SerialTransport& operator=(const SerialTransport&) = delete;

    Bytes query(const Bytes& request, std::chrono::milliseconds timeout) override;
    void reset() override { close_fd(); }

private:
    void open_fd();
    void close_fd() noexcept;
    Bytes exchange(const Bytes& request, std::chrono::milliseconds timeout);

    std::string path_;
    int baud_;
    int fd_ = -1;
    std::chrono::microseconds frame_gap_;
};

struct Reading {
    std::optional<std::vector<double>> values;  // nullopt on any failure — never a sentinel
    std::string error;
    [[nodiscard]] bool ok() const noexcept { return values.has_value(); }
};

class ModbusReader {
public:
    ModbusReader(Transport& t, std::uint8_t slave, std::uint16_t start, std::uint16_t count,
                 double scale = 10.0, bool is_signed = true,
                 std::chrono::milliseconds timeout = std::chrono::milliseconds(500));
    Reading read();
    [[nodiscard]] unsigned consecutive_failures() const noexcept { return consecutive_failures_; }

private:
    Reading fail(std::string why);
    Transport& transport_;
    std::uint8_t slave_;
    std::uint16_t count_;
    double scale_;
    bool signed_;
    std::chrono::milliseconds timeout_;
    Bytes request_;
    unsigned consecutive_failures_ = 0;
};

}  // namespace edgekit
