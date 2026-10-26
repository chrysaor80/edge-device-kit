"""Differential tests: the C++ port must agree with the Python reference on every input.

The C++ code was produced with an AI coding assistant. Unit tests check the cases I
thought of; these tests check the cases nobody thought of, by generating inputs and
requiring both implementations to give the same answer.

    EDGEKIT_BUILD=cpp/build pytest tests_cross
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from edgekit import crc, lenframe, modbus_rtu as mb, sensirion as sn  # noqa: E402

BUILD = Path(os.environ.get("EDGEKIT_BUILD", ROOT / "cpp" / "build"))
LIB_PATH = BUILD / "libedgekit_c.so"
if not LIB_PATH.exists():
    pytest.skip(f"C++ library not built: {LIB_PATH}", allow_module_level=True)

lib = ctypes.CDLL(str(LIB_PATH))
u8p = ctypes.POINTER(ctypes.c_uint8)
lib.ek_crc16.restype = ctypes.c_uint16
lib.ek_crc16.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
lib.ek_crc8.restype = ctypes.c_uint8
lib.ek_crc8.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
lib.ek_parse_read.restype = ctypes.c_int
lib.ek_parse_read.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint8, ctypes.c_uint16,
                              ctypes.POINTER(ctypes.c_uint16), u8p]
lib.ek_slave_reply.restype = ctypes.c_size_t
lib.ek_slave_reply.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint8, ctypes.c_uint32,
                               ctypes.c_char_p, ctypes.c_size_t]
lib.ek_lenframe_decode.restype = ctypes.c_int
lib.ek_lenframe_decode.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_char_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
lib.ek_scd41.restype = ctypes.c_int
lib.ek_scd41.argtypes = [ctypes.c_uint16] * 3 + [ctypes.POINTER(ctypes.c_double)] * 2

SETTINGS = settings(max_examples=3000, deadline=None)


def cpp_parse(frame: bytes, slave: int, count: int):
    regs = (ctypes.c_uint16 * count)()
    exc = ctypes.c_uint8()
    status = lib.ek_parse_read(frame, len(frame), slave, count, regs, ctypes.byref(exc))
    return status, list(regs) if status == 0 else None, exc.value


def py_parse(frame: bytes, slave: int, count: int):
    try:
        return "ok", mb.parse_read_response(frame, slave, count), None
    except mb.ModbusError as e:
        return "error", None, e.exception_code


@SETTINGS
@given(st.binary(max_size=300))
def test_checksums_agree(data):
    assert lib.ek_crc16(data, len(data)) == crc.crc16_modbus(data)
    assert lib.ek_crc8(data, len(data)) == crc.crc8_sensirion(data)


@st.composite
def reply_case(draw):
    """(frame, slave, count): mostly near-valid replies with matching slave/count, so the
    'accepted' path is exercised as hard as the 'rejected' paths."""
    if draw(st.integers(0, 4)) == 0:
        return draw(st.binary(max_size=60)), draw(st.integers(1, 247)), draw(st.integers(1, 20))
    regs = draw(st.lists(st.integers(0, 0xFFFF), min_size=1, max_size=20))
    slave = draw(st.integers(1, 247))
    frame = bytearray(mb.ModbusRtuSlave(slave, dict(enumerate(regs))).handle_request(
        mb.build_read_holding(slave, 0, len(regs))))
    mutation = draw(st.sampled_from(["none", "flip", "truncate", "extend", "count", "slave", "recrc"]))
    if mutation == "flip":
        frame[draw(st.integers(0, len(frame) - 1))] ^= draw(st.integers(1, 255))
    elif mutation == "truncate":
        del frame[draw(st.integers(0, len(frame) - 1)):]
    elif mutation == "extend":
        frame += draw(st.binary(min_size=1, max_size=4))
    elif mutation == "recrc":  # corrupt the body but fix the CRC: only structural checks can catch it
        frame[2] = draw(st.integers(0, 255))
        frame = bytearray(crc.append_crc16(bytes(frame[:-2])))
    count = len(regs) + (draw(st.integers(-1, 1)) if mutation == "count" else 0)
    slave_arg = (slave % 247) + 1 if mutation == "slave" else slave
    return bytes(frame), slave_arg, max(1, count)


@SETTINGS
@given(reply_case())
def test_read_response_verdicts_agree(case):
    frame, slave, count = case
    c_status, c_regs, c_exc = cpp_parse(frame, slave, count)
    p_verdict, p_regs, p_exc = py_parse(frame, slave, count)
    assert (c_status == 0) == (p_verdict == "ok"), (frame.hex(), c_status)
    assert c_regs == p_regs
    if c_status == 5:  # ExceptionReply
        assert c_exc == p_exc


@SETTINGS
@given(st.binary(min_size=8, max_size=8) | st.builds(
    lambda s, f, a, n: crc.append_crc16(bytes([s, f]) + a.to_bytes(2, "big") + n.to_bytes(2, "big")),
    st.integers(0, 3), st.sampled_from([3, 6, 4, 0x10]), st.integers(0, 70), st.integers(0, 130)),
    st.integers(1, 3), st.integers(1, 64))
def test_slave_replies_agree(request, slave_id, size):
    py = mb.ModbusRtuSlave(slave_id, {i: (i * 7) & 0xFFFF for i in range(size)}, size=size)
    out = ctypes.create_string_buffer(512)
    n = lib.ek_slave_reply(request, len(request), slave_id, size, out, 512)
    assert out.raw[:n] == py.handle_request(request)


@SETTINGS
@given(st.lists(st.one_of(st.binary(max_size=12), st.builds(lenframe.build, st.integers(0, 255), st.integers(0, 255),
                                                                st.binary(max_size=8))), max_size=8),
       st.integers(1, 9))
def test_stream_decoders_agree(pieces, chunk):
    stream = b"".join(pieces)
    dec, py_frames = lenframe.LenFrameDecoder(), []
    for i in range(0, len(stream), chunk):
        py_frames += dec.feed(stream[i:i + chunk])
    out, out_len = ctypes.create_string_buffer(4096), ctypes.c_size_t()
    n = lib.ek_lenframe_decode(stream, len(stream), chunk, out, 4096, ctypes.byref(out_len))
    assert n == len(py_frames)
    assert out.raw[:out_len.value] == b"".join(f.encode() for f in py_frames)


@SETTINGS
@given(st.integers(0, 0xFFFF), st.integers(0, 0xFFFF), st.integers(0, 0xFFFF))
def test_scd41_conversion_agrees_and_refuses_zero(co2, t, rh):
    temp, hum = ctypes.c_double(), ctypes.c_double()
    ok = lib.ek_scd41(co2, t, rh, ctypes.byref(temp), ctypes.byref(hum))
    assert ok == (co2 != 0)
    if ok:
        _, pt, prh = sn.scd41_convert(co2, t, rh)
        assert (temp.value, hum.value) == (pt, prh)  # bit-identical doubles expected


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pty")
def test_cpp_cli_reads_the_python_simulator_over_a_serial_line():
    from edgekit.sim_pty import PtyRoaster

    sim = PtyRoaster(slave_id=1, speed=60.0).start()
    try:
        time.sleep(0.2)
        out = subprocess.run([str(BUILD / "edgekit_poll"), sim.path, "--n", "3", "--period-ms", "200"],
                             capture_output=True, text=True, timeout=20)
        lines = [json.loads(line) for line in out.stdout.splitlines()]
        assert out.returncode == 0, out.stderr
        assert len(lines) == 3 and all(x["ok"] for x in lines), lines
        bt, et = lines[-1]["values"]
        assert 50 < bt < 250 and 100 < et < 450
        wrong = subprocess.run([str(BUILD / "edgekit_poll"), sim.path, "--slave", "9", "--n", "1"],
                               capture_output=True, text=True, timeout=10)
        assert json.loads(wrong.stdout) == {"ok": False, "error": "timeout"}
    finally:
        sim.stop()
