"""End-to-end over a real serial line discipline: reader -> pyserial -> pty -> simulated roaster."""

import sys
import time

import pytest

pytest.importorskip("serial")
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pseudo-terminal")

from edgekit.modbus_rtu import build_write_single, parse_write_response  # noqa: E402
from edgekit.reader import ModbusReader  # noqa: E402
from edgekit.sim_pty import REG_GAS, PtyRoaster  # noqa: E402
from edgekit.transport import SerialTransport  # noqa: E402


@pytest.fixture
def roaster():
    sim = PtyRoaster(slave_id=1, speed=60.0).start()
    yield sim
    sim.stop()


def test_reads_plausible_temperatures_over_serial(roaster):
    reader = ModbusReader(SerialTransport(roaster.path), slave_id=1, count=2, timeout=1.0)
    readings = []
    for _ in range(3):
        r = reader.read()
        assert r.ok, r.error
        readings.append(r.values)
        time.sleep(0.2)
    bt, et = readings[-1]
    assert 50 < bt < 250 and 100 < et < 450


def test_write_then_read_back_over_serial(roaster):
    t = SerialTransport(roaster.path)
    req = build_write_single(1, REG_GAS, 80)
    parse_write_response(t.query(req, 1.0), 1, REG_GAS, 80)
    assert roaster.slave.registers[REG_GAS] == 80


def test_wrong_slave_times_out_cleanly(roaster):
    r = ModbusReader(SerialTransport(roaster.path), slave_id=9, timeout=0.3).read()
    assert not r.ok and r.error == "timeout"
