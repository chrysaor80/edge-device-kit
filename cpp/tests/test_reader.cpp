// Reader contract under injected faults (same scenarios as python/tests/test_reader_faults.py).
#include <gtest/gtest.h>

#include <functional>
#include <ostream>
#include <string>

#include "edgekit/transport.hpp"

using namespace edgekit;
namespace mb = edgekit::modbus;

namespace {

// Loopback transport: runs a simulated slave, applies one scripted fault per call.
class FakeTransport : public Transport {
public:
    using Fault = std::function<Bytes(Bytes)>;  // mangles the reply; throw Disconnected to drop link
    explicit FakeTransport(std::vector<Fault> script) : script_(std::move(script)), slave_(1) {
        slave_.registers()[0] = 1805;
        slave_.registers()[1] = 2050;
    }
    Bytes query(const Bytes& req, std::chrono::milliseconds) override {
        Bytes reply = slave_.handle_request(req);
        const std::size_t n = call_++;  // count the call before a fault can throw
        if (n < script_.size()) reply = script_[n](reply);
        return reply;
    }
    void reset() override { ++resets; }
    mb::Slave& slave() { return slave_; }
    int resets = 0;

private:
    std::vector<Fault> script_;
    std::size_t call_ = 0;
    mb::Slave slave_;
};

const FakeTransport::Fault kDropout = [](Bytes) { return Bytes{}; };
const FakeTransport::Fault kBadCrc = [](Bytes b) { b.back() ^= 0xFF; return b; };
const FakeTransport::Fault kPartial = [](Bytes b) { b.resize(4); return b; };
const FakeTransport::Fault kGarbage = [](Bytes b) { b.insert(b.begin(), {0xAB, 0xCD}); return b; };
const FakeTransport::Fault kDisconnect = [](Bytes) -> Bytes { throw Disconnected("usb gone"); };

}  // namespace

TEST(Reader, NormalRead) {
    FakeTransport t({});
    ModbusReader r(t, 1, 0, 2);
    const auto v = r.read();
    ASSERT_TRUE(v.ok());
    EXPECT_EQ(*v.values, (std::vector<double>{180.5, 205.0}));
}

struct NamedFault {
    const char* name;
    FakeTransport::Fault fault;
};
void PrintTo(const NamedFault& f, std::ostream* os) { *os << f.name; }

class ReaderFault : public ::testing::TestWithParam<NamedFault> {};

TEST_P(ReaderFault, FailureIsExplicitAndNextCycleRecovers) {
    FakeTransport t({GetParam().fault});
    ModbusReader r(t, 1, 0, 2);
    const auto bad = r.read();
    EXPECT_FALSE(bad.ok());
    EXPECT_FALSE(bad.error.empty());
    const auto good = r.read();
    ASSERT_TRUE(good.ok()) << good.error;
    EXPECT_EQ(r.consecutive_failures(), 0U);
}

INSTANTIATE_TEST_SUITE_P(Faults, ReaderFault,
                         ::testing::Values(NamedFault{"dropout", kDropout}, NamedFault{"bad_crc", kBadCrc},
                                           NamedFault{"partial", kPartial}, NamedFault{"garbage", kGarbage},
                                           NamedFault{"disconnect", kDisconnect}),
                         [](const auto& info) { return std::string(info.param.name); });

TEST(Reader, DisconnectResetsTheTransport) {
    FakeTransport t({kDisconnect});
    ModbusReader r(t, 1, 0, 2);
    r.read();
    EXPECT_EQ(t.resets, 1);
}

TEST(Reader, NegativeValuesAreSigned) {
    FakeTransport t({});
    t.slave().registers()[0] = static_cast<std::uint16_t>(-125);
    ModbusReader r(t, 1, 0, 2);
    EXPECT_DOUBLE_EQ((*r.read().values)[0], -12.5);
}
