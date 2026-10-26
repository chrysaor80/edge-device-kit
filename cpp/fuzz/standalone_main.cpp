// Fallback driver for compilers without libFuzzer (e.g. GCC): feeds the same
// LLVMFuzzerTestOneInput with random and mutated-seed inputs. Coverage-guided
// fuzzing still runs in CI with clang; this keeps the invariants checked everywhere.
//
//   fuzz_smoke [iterations] [seed]
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <random>
#include <vector>

#include "edgekit/lenframe.hpp"
#include "edgekit/modbus.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size);

int main(int argc, char** argv) {
    const long iterations = argc > 1 ? std::atol(argv[1]) : 200000;
    std::mt19937 rng(argc > 2 ? static_cast<unsigned>(std::atol(argv[2])) : 1U);

    // Valid frames as seeds, so mutations explore near-valid input (where bugs live).
    std::vector<edgekit::Bytes> seeds;
    edgekit::modbus::Slave slave(1);
    for (std::uint16_t c = 1; c <= 8; ++c) {
        edgekit::Bytes s{1, static_cast<std::uint8_t>(c - 1)};
        const auto reply = slave.handle_request(edgekit::modbus::build_read_holding(1, 0, c));
        s.insert(s.end(), reply.begin(), reply.end());
        seeds.push_back(s);
    }
    seeds.push_back(edgekit::lenframe::build(5, 0x20, {1, 2, 3}));

    std::vector<std::uint8_t> buf;
    for (long i = 0; i < iterations; ++i) {
        if (i % 2 == 0) {  // pure random
            buf.resize(rng() % 300);
            for (auto& b : buf) b = static_cast<std::uint8_t>(rng());
        } else {           // mutated seed: flip, insert, delete, truncate
            buf = seeds[rng() % seeds.size()];
            const int edits = 1 + static_cast<int>(rng() % 4);
            for (int e = 0; e < edits && !buf.empty(); ++e) {
                const std::size_t pos = rng() % buf.size();
                switch (rng() % 4) {
                    case 0: buf[pos] ^= static_cast<std::uint8_t>(1U << (rng() % 8)); break;
                    case 1: buf.insert(buf.begin() + static_cast<std::ptrdiff_t>(pos), static_cast<std::uint8_t>(rng())); break;
                    case 2: buf.erase(buf.begin() + static_cast<std::ptrdiff_t>(pos)); break;
                    default: buf.resize(pos); break;
                }
            }
        }
        LLVMFuzzerTestOneInput(buf.data(), buf.size());
    }
    std::printf("fuzz_smoke: %ld inputs, no invariant violated\n", iterations);
    return 0;
}
