#include <gtest/gtest.h>

#include <cstring>
#include <random>

#include "edgekit/crc.hpp"
#include "edgekit/lenframe.hpp"
#include "edgekit/modbus.hpp"
#include "edgekit/sensirion.hpp"

using namespace edgekit;
namespace mb = edgekit::modbus;

namespace {
Bytes str(const char* s) { return Bytes(s, s + std::strlen(s)); }
}  // namespace

TEST(Crc, CatalogueCheckValues) {
    const Bytes v = str("123456789");
    EXPECT_EQ(crc16_modbus(v), 0x4B37);                       // CRC-16/MODBUS
    EXPECT_EQ(crc8_sensirion(v.data(), v.size()), 0xF7);      // CRC-8/NRSC-5
    const std::uint8_t beef[] = {0xBE, 0xEF};
    EXPECT_EQ(crc8_sensirion(beef, 2), 0x92);                 // Sensirion datasheet example
}

TEST(Modbus, KnownRequestBytes) {
    EXPECT_EQ(mb::build_read_holding(1, 0, 10), (Bytes{0x01, 0x03, 0x00, 0x00, 0x00, 0x0A, 0xC5, 0xCD}));
}

TEST(Modbus, BuilderRejectsInvalidInput) {
    EXPECT_THROW(mb::build_read_holding(0, 0, 1), std::invalid_argument);
    EXPECT_THROW(mb::build_read_holding(248, 0, 1), std::invalid_argument);
    EXPECT_THROW(mb::build_read_holding(1, 0, 0), std::invalid_argument);
    EXPECT_THROW(mb::build_read_holding(1, 0, 126), std::invalid_argument);
    EXPECT_THROW(mb::build_read_holding(1, 0xFFFF, 2), std::invalid_argument);
}

TEST(Modbus, RoundTripThroughSlave) {
    mb::Slave s(1);
    s.registers()[0] = 1805;
    s.registers()[1] = 2050;
    const auto r = mb::parse_read_response(s.handle_request(mb::build_read_holding(1, 0, 2)), 1, 2);
    ASSERT_TRUE(r.ok()) << mb::to_string(r.status);
    EXPECT_EQ(r.registers, (std::vector<std::uint16_t>{1805, 2050}));
}

TEST(Modbus, DamagedFramesAreRejectedWithTheRightReason) {
    mb::Slave s(1);
    const Bytes good = s.handle_request(mb::build_read_holding(1, 0, 2));
    Bytes bad_crc = good;
    bad_crc.back() ^= 0xFF;
    EXPECT_EQ(mb::parse_read_response(bad_crc, 1, 2).status, mb::Status::BadCrc);
    EXPECT_EQ(mb::parse_read_response(Bytes(good.begin(), good.begin() + 4), 1, 2).status, mb::Status::TooShort);
    EXPECT_EQ(mb::parse_read_response(good, 2, 2).status, mb::Status::WrongSlave);
    EXPECT_EQ(mb::parse_read_response(good, 1, 3).status, mb::Status::BadByteCount);
    EXPECT_EQ(mb::parse_read_response(nullptr, 0, 1, 2).status, mb::Status::TooShort);
}

TEST(Modbus, ExceptionRepliesCarryTheirCode) {
    mb::Slave s(1, 16);
    const auto r = mb::parse_read_response(s.handle_request(mb::build_read_holding(1, 15, 2)), 1, 2);
    EXPECT_EQ(r.status, mb::Status::ExceptionReply);
    EXPECT_EQ(r.exception_code, mb::kIllegalDataAddress);
}

TEST(Modbus, SlaveIsSilentForOtherAddressOrBadCrc) {
    mb::Slave s(1);
    EXPECT_TRUE(s.handle_request(mb::build_read_holding(9, 0, 2)).empty());
    Bytes req = mb::build_read_holding(1, 0, 2);
    req.back() ^= 1;
    EXPECT_TRUE(s.handle_request(req).empty());
}

TEST(Modbus, WriteSingleEchoesAndStores) {
    mb::Slave s(1);
    const Bytes reply = s.handle_request(mb::build_write_single(1, 10, 70));
    EXPECT_EQ(mb::parse_write_response(reply.data(), reply.size(), 1, 10, 70), mb::Status::Ok);
    EXPECT_EQ(mb::parse_write_response(reply.data(), reply.size(), 1, 10, 71), mb::Status::EchoMismatch);
    EXPECT_EQ(s.registers()[10], 70);
}

TEST(Modbus, RandomBlocksRoundTrip) {
    std::mt19937 rng(42);
    for (int trial = 0; trial < 500; ++trial) {
        const std::uint16_t count = 1 + rng() % mb::kMaxReadCount;
        mb::Slave s(7);
        std::vector<std::uint16_t> expect;
        for (std::uint16_t i = 0; i < count; ++i) {
            expect.push_back(static_cast<std::uint16_t>(rng()));
            s.registers()[i] = expect.back();
        }
        const auto r = mb::parse_read_response(s.handle_request(mb::build_read_holding(7, 0, count)), 7, count);
        ASSERT_TRUE(r.ok());
        ASSERT_EQ(r.registers, expect);
    }
}

TEST(LenFrame, RoundTrip) {
    const Bytes data{0x2C, 0x01};
    const auto f = lenframe::parse(lenframe::build(3, 0x20, data).data(), 7);
    ASSERT_TRUE(f.has_value());
    EXPECT_EQ(*f, (lenframe::Frame{3, 0x20, data}));
}

TEST(LenFrame, DecoderResyncsAfterGarbageWithBogusLength) {
    // 0x13 in the garbage claims a 19-byte payload we will never receive.
    Bytes stream{0xFF, 0x00, 0x13};
    const Bytes a = lenframe::build(1, 0x20, {0x2C, 0x01});
    const Bytes b = lenframe::build(2, 0x20, {0x10, 0x00});
    stream.insert(stream.end(), a.begin(), a.end());
    stream.push_back(0x55);
    stream.insert(stream.end(), b.begin(), b.end());
    lenframe::Decoder dec;
    std::vector<lenframe::Frame> got;
    for (std::size_t i = 0; i < stream.size(); i += 3) {
        const auto out = dec.feed(stream.data() + i, std::min<std::size_t>(3, stream.size() - i));
        got.insert(got.end(), out.begin(), out.end());
    }
    ASSERT_EQ(got.size(), 2U);
    EXPECT_EQ(got[0].addr, 1);
    EXPECT_EQ(got[1].addr, 2);
    EXPECT_EQ(dec.pending(), 0U);
}

TEST(Sensirion, WordCrcAndConversions) {
    const std::uint8_t ok[] = {0xBE, 0xEF, 0x92};
    const std::uint8_t bad[] = {0xBE, 0xEF, 0x00};
    EXPECT_EQ(sensirion::read_word(ok), 0xBEEF);
    EXPECT_FALSE(sensirion::read_word(bad).has_value());
    const auto scd = sensirion::scd41_convert(400, 0x8000, 0x8000);
    ASSERT_TRUE(scd.has_value());
    EXPECT_DOUBLE_EQ(scd->temp_c, 42.5);
    EXPECT_DOUBLE_EQ(scd->rh_pct, 50.0);
    EXPECT_FALSE(sensirion::scd41_convert(0, 0x8000, 0x8000).has_value());  // no fake 0 ppm
    EXPECT_NEAR(sensirion::sht4x_convert(0x8000, 0).temp_c, 42.5013, 1e-4);
    EXPECT_EQ(sensirion::sht4x_convert(0, 0xFFFF).rh_pct, 100.0);
}
