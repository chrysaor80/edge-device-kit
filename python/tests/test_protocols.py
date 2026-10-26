"""Codec tests against published reference values, plus property-based round trips."""

import pytest
from hypothesis import given, strategies as st

from edgekit import crc, lenframe, modbus_rtu as mb, sensirion as sn


# ── CRC reference vectors ────────────────────────────────────────────────
def test_crc16_catalogue_check_value():
    # CRC RevEng catalogue, CRC-16/MODBUS check value for ASCII "123456789"
    assert crc.crc16_modbus(b"123456789") == 0x4B37


def test_crc16_known_modbus_request():
    # widely published example: read 10 holding registers from slave 1
    assert mb.build_read_holding(1, 0, 10) == bytes.fromhex("01 03 00 00 00 0A C5 CD")


def test_crc8_sensirion_datasheet_example():
    assert crc.crc8_sensirion(b"\xBE\xEF") == 0x92          # SCD4x / SHT4x datasheet
    assert crc.crc8_sensirion(b"123456789") == 0xF7         # catalogue name: CRC-8/NRSC-5


# ── Modbus RTU ──────────────────────────────────────────────────────────
def test_round_trip_through_slave():
    slave = mb.ModbusRtuSlave(1, {0: 1805, 1: 2050})
    assert mb.parse_read_response(slave.handle_request(mb.build_read_holding(1, 0, 2)), 1, 2) == [1805, 2050]


def test_write_single_echo_and_effect():
    slave = mb.ModbusRtuSlave(1)
    req = mb.build_write_single(1, 10, 70)
    mb.parse_write_response(slave.handle_request(req), 1, 10, 70)
    assert slave.registers[10] == 70


@pytest.mark.parametrize("mangle, reason", [
    (lambda f: f[:-1] + bytes([f[-1] ^ 0xFF]), "CRC"),
    (lambda f: f[:4], "too short"),
    (lambda f: f + b"\x00", "length"),
])
def test_parser_rejects_damaged_frames(mangle, reason):
    good = mb.ModbusRtuSlave(1, {0: 1, 1: 2}).handle_request(mb.build_read_holding(1, 0, 2))
    with pytest.raises(mb.ModbusError):
        mb.parse_read_response(mangle(good), 1, 2)


def test_exception_response_is_reported_with_code():
    slave = mb.ModbusRtuSlave(1)
    too_many = crc.append_crc16(bytes([1, 0x03, 0, 0, 0, 200]))  # 200 > 125 registers
    with pytest.raises(mb.ModbusError) as err:
        mb.parse_read_response(slave.handle_request(too_many), 1, 200)
    assert err.value.exception_code == mb.ILLEGAL_DATA_VALUE


def test_unsupported_function_gets_illegal_function():
    reply = mb.ModbusRtuSlave(1).handle_request(crc.append_crc16(bytes([1, 0x04, 0, 0, 0, 2])))
    assert reply[1] == 0x84 and reply[2] == mb.ILLEGAL_FUNCTION and crc.check_crc16(reply)


def test_reply_to_a_different_function_is_not_accepted_as_data():
    reply = mb.ModbusRtuSlave(1).handle_request(crc.append_crc16(bytes([1, 0x04, 0, 0, 0, 2])))
    with pytest.raises(mb.ModbusError, match="unexpected function"):
        mb.parse_read_response(reply, 1, 2)


def test_slave_is_silent_for_other_address_and_bad_crc():
    slave = mb.ModbusRtuSlave(1)
    assert slave.handle_request(mb.build_read_holding(9, 0, 2)) == b""
    req = bytearray(mb.build_read_holding(1, 0, 2)); req[-1] ^= 1
    assert slave.handle_request(bytes(req)) == b""


def test_out_of_range_read_gives_illegal_address():
    slave = mb.ModbusRtuSlave(1, size=16)
    with pytest.raises(mb.ModbusError) as err:
        mb.parse_read_response(slave.handle_request(mb.build_read_holding(1, 15, 2)), 1, 2)
    assert err.value.exception_code == mb.ILLEGAL_DATA_ADDRESS


@pytest.mark.parametrize("args", [(0, 0, 1), (248, 0, 1), (1, 0, 0), (1, 0, 126), (1, 0xFFFF, 2)])
def test_builder_rejects_invalid_requests(args):
    with pytest.raises(ValueError):
        mb.build_read_holding(*args)


@given(st.lists(st.integers(0, 0xFFFF), min_size=1, max_size=125))
def test_any_register_block_round_trips(values):
    slave = mb.ModbusRtuSlave(7, dict(enumerate(values)))
    resp = slave.handle_request(mb.build_read_holding(7, 0, len(values)))
    assert mb.parse_read_response(resp, 7, len(values)) == values


@given(st.binary(max_size=300))
def test_parser_never_crashes_on_garbage(blob):
    try:
        mb.parse_read_response(blob, 1, 2)
    except mb.ModbusError:
        pass


# ── Length-prefixed vendor frames ───────────────────────────────────────
@given(st.integers(0, 255), st.integers(0, 255), st.binary(max_size=lenframe.MAX_DATA))
def test_lenframe_round_trip(addr, cmd, data):
    assert lenframe.parse(lenframe.build(addr, cmd, data)) == lenframe.LenFrame(addr, cmd, data)


def test_decoder_resyncs_after_garbage_and_split_reads():
    a = lenframe.build(1, 0x20, b"\x2c\x01")
    b = lenframe.build(2, 0x20, b"\x10\x00")
    dec = lenframe.LenFrameDecoder()
    stream = b"\xff\x00\x13" + a + b"\x55" + b
    frames = []
    for i in range(0, len(stream), 3):  # arrive in 3-byte pieces
        frames += dec.feed(stream[i:i + 3])
    assert [f.addr for f in frames] == [1, 2]
    assert lenframe.decode_le(frames[0].data) == 300
    assert dec.dropped_bytes == 4 and dec.pending == 0


@given(st.binary(max_size=200), st.integers(1, 7))
def test_decoder_finds_frame_after_any_garbage(prefix, chunk):
    """Whatever line noise precedes a frame, the frame must come out as soon as its
    last byte arrives — even if the noise contains a bogus length byte."""
    frame = lenframe.build(5, 0x20, b"\x01\x02")
    dec, stream, found = lenframe.LenFrameDecoder(), prefix + frame, []
    for i in range(0, len(stream), chunk):
        found += dec.feed(stream[i:i + chunk])
    assert lenframe.LenFrame(5, 0x20, b"\x01\x02") in found


# ── Sensirion conversions ───────────────────────────────────────────────
def test_sensirion_word_crc():
    assert sn.read_word(sn.word_with_crc(0xBEEF)) == 0xBEEF
    with pytest.raises(ValueError):
        sn.read_word(b"\xBE\xEF\x00")


def test_conversion_denominators_differ_between_sensor_families():
    _, t_scd, _ = sn.scd41_convert(400, 0x8000, 0)
    t_sht, _ = sn.sht4x_convert(0x8000, 0)
    assert t_scd == pytest.approx(42.5)
    assert t_sht == pytest.approx(42.5013, abs=1e-4)  # 65535 vs 65536


def test_sht4x_humidity_is_clamped():
    assert sn.sht4x_convert(0, 0)[1] == 0.0
    assert sn.sht4x_convert(0, 0xFFFF)[1] == 100.0
