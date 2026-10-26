#include "edgekit/transport.hpp"

#include <fcntl.h>
#include <poll.h>
#include <termios.h>
#include <unistd.h>

#include <cerrno>
#include <system_error>
#include <utility>

namespace edgekit {
namespace {

speed_t to_speed(int baud) {
    switch (baud) {
        case 9600: return B9600;
        case 19200: return B19200;
        case 38400: return B38400;
        case 57600: return B57600;
        case 115200: return B115200;
        default: throw std::invalid_argument("unsupported baud rate");
    }
}

[[noreturn]] void throw_errno(const std::string& what) {
    throw Disconnected(what + ": " + std::generic_category().message(errno));
}

}  // namespace

SerialTransport::SerialTransport(std::string path, int baud) : path_(std::move(path)), baud_(baud) {
    to_speed(baud_);  // validate early
    const double char_us = 10.0 * 1e6 / baud_;  // 8N1 = 10 bits per character
    frame_gap_ = baud_ > 19200 ? std::chrono::microseconds(1750)
                               : std::chrono::microseconds(static_cast<long>(3.5 * char_us));
}

SerialTransport::~SerialTransport() { close_fd(); }

void SerialTransport::close_fd() noexcept {
    if (fd_ >= 0) ::close(fd_);
    fd_ = -1;
}

void SerialTransport::open_fd() {
    if (fd_ >= 0) return;
    const int fd = ::open(path_.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) throw_errno("open " + path_);
    termios tio{};
    if (::tcgetattr(fd, &tio) != 0) { ::close(fd); throw_errno("tcgetattr"); }
    ::cfmakeraw(&tio);
    tio.c_cflag |= CLOCAL | CREAD;
    tio.c_cflag &= ~static_cast<tcflag_t>(CSTOPB | PARENB);  // 8N1
    ::cfsetispeed(&tio, to_speed(baud_));
    ::cfsetospeed(&tio, to_speed(baud_));
    if (::tcsetattr(fd, TCSANOW, &tio) != 0) { ::close(fd); throw_errno("tcsetattr"); }
    fd_ = fd;
}

Bytes SerialTransport::exchange(const Bytes& request, std::chrono::milliseconds timeout) {
    using clock = std::chrono::steady_clock;
    open_fd();
    if (::tcflush(fd_, TCIFLUSH) != 0) throw_errno("tcflush");
    std::size_t sent = 0;
    while (sent < request.size()) {
        const ssize_t n = ::write(fd_, request.data() + sent, request.size() - sent);
        if (n < 0) {
            if (errno == EAGAIN || errno == EINTR) continue;
            throw_errno("write");
        }
        sent += static_cast<std::size_t>(n);
    }
    ::tcdrain(fd_);

    Bytes reply;
    const auto deadline = clock::now() + timeout;
    while (true) {
        // Before the first byte wait until the deadline; after it, until the line goes quiet.
        const auto wait = reply.empty()
            ? std::chrono::duration_cast<std::chrono::milliseconds>(deadline - clock::now())
            : std::chrono::duration_cast<std::chrono::milliseconds>(frame_gap_) + std::chrono::milliseconds(1);
        if (wait.count() <= 0) return reply;
        pollfd pfd{fd_, POLLIN, 0};
        const int rc = ::poll(&pfd, 1, static_cast<int>(wait.count()));
        if (rc < 0) {
            if (errno == EINTR) continue;
            throw_errno("poll");
        }
        if (rc == 0) return reply;  // timeout, or silence after data = end of frame
        if ((pfd.revents & (POLLERR | POLLHUP | POLLNVAL)) != 0) throw Disconnected("serial line hung up");
        std::uint8_t buf[256];
        const ssize_t n = ::read(fd_, buf, sizeof buf);
        if (n < 0) {
            if (errno == EAGAIN || errno == EINTR) continue;
            throw_errno("read");
        }
        reply.insert(reply.end(), buf, buf + n);
        if (reply.size() > 256) return reply;  // longer than any RTU frame: stop reading noise
    }
}

Bytes SerialTransport::query(const Bytes& request, std::chrono::milliseconds timeout) {
    try {
        return exchange(request, timeout);
    } catch (const Disconnected&) {
        close_fd();  // stale descriptor after USB re-enumeration: reopen once
    }
    try {
        return exchange(request, timeout);
    } catch (const Disconnected&) {
        close_fd();
        throw;
    }
}

ModbusReader::ModbusReader(Transport& t, std::uint8_t slave, std::uint16_t start,
                           std::uint16_t count, double scale, bool is_signed,
                           std::chrono::milliseconds timeout)
    : transport_(t), slave_(slave), count_(count), scale_(scale), signed_(is_signed),
      timeout_(timeout), request_(modbus::build_read_holding(slave, start, count)) {}

Reading ModbusReader::fail(std::string why) {
    ++consecutive_failures_;
    return Reading{std::nullopt, std::move(why)};
}

Reading ModbusReader::read() {
    Bytes reply;
    try {
        reply = transport_.query(request_, timeout_);
    } catch (const Disconnected& e) {
        transport_.reset();
        return fail(std::string("disconnected: ") + e.what());
    }
    if (reply.empty()) return fail("timeout");
    const auto r = modbus::parse_read_response(reply, slave_, count_);
    if (!r.ok()) return fail(modbus::to_string(r.status));
    std::vector<double> values;
    values.reserve(r.registers.size());
    for (const std::uint16_t reg : r.registers) {
        const int v = (signed_ && (reg & 0x8000U) != 0U) ? static_cast<int>(reg) - 0x10000 : static_cast<int>(reg);
        values.push_back(v / scale_);
    }
    consecutive_failures_ = 0;
    return Reading{std::move(values), {}};
}

}  // namespace edgekit
