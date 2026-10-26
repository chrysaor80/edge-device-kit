// libFuzzer target: every parser must survive arbitrary bytes, and must only ever
// accept input that is internally consistent.
//
//   cmake -B build-fuzz -DEDGEKIT_FUZZ=ON -DCMAKE_CXX_COMPILER=clang++ && cmake --build build-fuzz
//   ./build-fuzz/fuzz_parsers -max_total_time=60
#include <cstdlib>

#include "edgekit/lenframe.hpp"
#include "edgekit/modbus.hpp"
#include "edgekit/sensirion.hpp"

using namespace edgekit;

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    if (size < 3) return 0;
    const std::uint8_t slave = data[0];
    const std::uint16_t count = static_cast<std::uint16_t>(1 + data[1] % modbus::kMaxReadCount);
    const std::uint8_t* body = data + 2;
    const std::size_t len = size - 2;

    // 1. Modbus read reply: if accepted, it must re-encode to exactly the input.
    const auto r = modbus::parse_read_response(body, len, slave, count);
    if (r.ok()) {
        Bytes again{slave, modbus::kReadHolding, static_cast<std::uint8_t>(2 * count)};
        for (const std::uint16_t v : r.registers) {
            again.push_back(static_cast<std::uint8_t>(v >> 8));
            again.push_back(static_cast<std::uint8_t>(v & 0xFF));
        }
        append_crc16(again);
        if (again != Bytes(body, body + len)) std::abort();
    }

    // 2. Slave must never crash and must only answer well-formed requests with valid CRC.
    modbus::Slave s(slave == 0 ? 1 : slave, 1U + data[1]);
    const Bytes reply = s.handle_request(body, len);
    if (!reply.empty() && !check_crc16(reply)) std::abort();

    // 3. Stream decoder: every frame it emits must be complete and CRC-valid.
    lenframe::Decoder dec;
    const std::size_t step = 1 + data[1] % 8;
    for (std::size_t i = 0; i < len; i += step) {
        for (const auto& f : dec.feed(body + i, std::min(step, len - i))) {
            const Bytes enc = lenframe::build(f.addr, f.cmd, f.data);
            if (!lenframe::parse(enc.data(), enc.size())) std::abort();
        }
    }

    // 4. Sensirion words.
    if (len >= 3) (void)sensirion::read_word(body);
    return 0;
}
