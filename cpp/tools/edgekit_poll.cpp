// edgekit_poll — read holding registers from a Modbus RTU device and print JSON lines.
//
//   edgekit_poll /dev/ttyUSB0 --slave 1 --start 0 --count 2 --scale 10 --n 5 --period-ms 1000
//
// A failed cycle prints {"ok":false,"error":"..."} — it never prints a made-up number.
#include <charconv>
#include <chrono>
#include <iomanip>
#include <iostream>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>

#include "edgekit/transport.hpp"

namespace {

long arg_long(int argc, char** argv, const std::string& name, long def, long lo, long hi) {
    for (int i = 2; i + 1 < argc; ++i) {
        if (name != argv[i]) continue;
        // std::from_chars: no errno, no locale, and it reports where parsing stopped
        const std::string_view text(argv[i + 1]);
        long v = 0;
        const auto [ptr, ec] = std::from_chars(text.data(), text.data() + text.size(), v);
        if (ec != std::errc() || ptr != text.data() + text.size() || v < lo || v > hi)
            throw std::invalid_argument(name + " must be an integer in [" + std::to_string(lo) + ", " +
                                        std::to_string(hi) + "]");
        return v;
    }
    return def;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr << "usage: " << argv[0]
                  << " PORT [--slave N] [--start N] [--count N] [--scale N] [--n CYCLES] [--period-ms MS] [--baud B]\n";
        return 2;
    }
    try {
        edgekit::SerialTransport port(argv[1], static_cast<int>(arg_long(argc, argv, "--baud", 9600, 1200, 115200)));
        edgekit::ModbusReader reader(
            port, static_cast<std::uint8_t>(arg_long(argc, argv, "--slave", 1, 1, 247)),
            static_cast<std::uint16_t>(arg_long(argc, argv, "--start", 0, 0, 0xFFFF)),
            static_cast<std::uint16_t>(arg_long(argc, argv, "--count", 2, 1, 125)),
            static_cast<double>(arg_long(argc, argv, "--scale", 10, 1, 100000)));
        const long cycles = arg_long(argc, argv, "--n", 1, 1, 1000000);
        const auto period = std::chrono::milliseconds(arg_long(argc, argv, "--period-ms", 1000, 0, 3600000));
        long failures = 0;
        std::cout << std::fixed << std::setprecision(1);
        for (long i = 0; i < cycles; ++i) {
            const auto r = reader.read();
            if (const auto& values = r.values) {
                std::cout << R"({"ok":true,"values":[)";
                for (std::size_t k = 0; k < values->size(); ++k) std::cout << (k > 0 ? "," : "") << (*values)[k];
                std::cout << "]}\n";
            } else {
                ++failures;
                std::cout << R"({"ok":false,"error":")" << r.error << "\"}\n";
            }
            std::cout.flush();
            if (i + 1 < cycles) std::this_thread::sleep_for(period);
        }
        return failures == cycles ? 1 : 0;
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << '\n';
        return 2;
    }
}
