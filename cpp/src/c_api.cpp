// Flat C interface used by tests_cross/ (Python ctypes) to compare the C++ and
// Python implementations on the same inputs. Not meant as a public API.
#include <cstring>

#include "edgekit/crc.hpp"
#include "edgekit/lenframe.hpp"
#include "edgekit/modbus.hpp"
#include "edgekit/sensirion.hpp"

using namespace edgekit;

extern "C" {

std::uint16_t ek_crc16(const std::uint8_t* d, std::size_t n) { return crc16_modbus(d, n); }
std::uint8_t ek_crc8(const std::uint8_t* d, std::size_t n) { return crc8_sensirion(d, n); }

// Returns the Status code; on Ok writes `count` registers to out_regs.
int ek_parse_read(const std::uint8_t* f, std::size_t n, std::uint8_t slave, std::uint16_t count,
                  std::uint16_t* out_regs, std::uint8_t* out_exc) {
    const auto r = modbus::parse_read_response(f, n, slave, count);
    if (r.ok() && out_regs != nullptr) std::memcpy(out_regs, r.registers.data(), r.registers.size() * 2);
    if (out_exc != nullptr) *out_exc = r.exception_code;
    return static_cast<int>(r.status);
}

// Slave with registers r[i] = (i * 7) & 0xFFFF over `size` registers. Returns reply length (0 = silent).
std::size_t ek_slave_reply(const std::uint8_t* req, std::size_t n, std::uint8_t id, std::uint32_t size,
                           std::uint8_t* out, std::size_t cap) {
    modbus::Slave s(id, size);
    for (std::uint32_t i = 0; i < size && i < 0x10000U; ++i)
        s.registers()[static_cast<std::uint16_t>(i)] = static_cast<std::uint16_t>((i * 7U) & 0xFFFFU);
    const Bytes reply = s.handle_request(req, n);
    if (reply.size() > cap) return 0;
    std::memcpy(out, reply.data(), reply.size());
    return reply.size();
}

// Feeds `n` bytes in `chunk`-sized pieces; writes decoded frames back-to-back (re-encoded)
// into out. Returns the number of frames, or -1 if `out` is too small.
int ek_lenframe_decode(const std::uint8_t* d, std::size_t n, std::size_t chunk, std::uint8_t* out,
                       std::size_t cap, std::size_t* out_len) {
    lenframe::Decoder dec;
    Bytes all;
    int frames = 0;
    if (chunk == 0) chunk = 1;
    for (std::size_t i = 0; i < n; i += chunk) {
        const std::size_t len = (n - i < chunk) ? n - i : chunk;
        for (const auto& f : dec.feed(d + i, len)) {
            const Bytes enc = lenframe::build(f.addr, f.cmd, f.data);
            all.insert(all.end(), enc.begin(), enc.end());
            ++frames;
        }
    }
    if (all.size() > cap) return -1;
    std::memcpy(out, all.data(), all.size());
    *out_len = all.size();
    return frames;
}

// Returns 1 and fills outputs on success, 0 for "no valid sample".
int ek_scd41(std::uint16_t co2, std::uint16_t t, std::uint16_t rh, double* temp, double* hum) {
    const auto s = sensirion::scd41_convert(co2, t, rh);
    if (!s) return 0;
    *temp = s->temp_c;
    *hum = s->rh_pct;
    return 1;
}

}  // extern "C"
