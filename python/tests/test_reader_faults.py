"""Reader contract under injected faults: correct values, or an explicit failure — never a fake number."""

import pytest

from edgekit import faults as F
from edgekit.modbus_rtu import ModbusRtuSlave
from edgekit.reader import ModbusReader
from edgekit.roaster_model import Controls, RoasterModel, find_first_crossing, find_turning_point
from edgekit.transport import LoopbackTransport


def make(schedule=None, regs=None):
    slave = ModbusRtuSlave(1, regs or {0: 1805, 1: 2050})
    transport = LoopbackTransport(slave, F.FaultSchedule(schedule or []))
    return ModbusReader(transport, slave_id=1, start=0, count=2, scale=10.0), transport


def test_normal_read():
    reader, _ = make()
    r = reader.read()
    assert r.ok and r.values == (180.5, 205.0)


@pytest.mark.parametrize("fault", [
    F.DROPOUT, F.BAD_CRC, F.partial(4), F.partial(0), F.garbage(), F.latency(2.0), F.DISCONNECT,
], ids=["dropout", "bad_crc", "partial", "empty", "garbage", "latency", "disconnect"])
def test_every_fault_is_an_explicit_failure_and_the_next_cycle_recovers(fault):
    reader, _ = make([fault])
    bad = reader.read()
    assert bad.ok is False and bad.values is None and bad.error
    good = reader.read()
    assert good.ok and good.values == (180.5, 205.0)
    assert reader.consecutive_failures == 0 and reader.failures == 1


def test_negative_temperatures_are_signed():
    reader, _ = make(regs={0: (-125) & 0xFFFF, 1: 50})
    assert reader.read().values == (-12.5, 5.0)


def test_burst_of_failures_is_counted():
    reader, _ = make([F.DROPOUT] * 5)
    for _ in range(5):
        assert not reader.read().ok
    assert reader.consecutive_failures == 5
    assert reader.read().ok


def test_roaster_model_has_a_plausible_shape():
    samples = RoasterModel(charge_weight_g=250).simulate(Controls(65, 30, 50), duration_s=780, dt=0.5)
    tp = find_turning_point(samples)
    assert 60 <= tp.t <= 180 and 70 <= tp.bt <= 140
    assert find_first_crossing(samples, 196.0, after_s=tp.t) is not None
    assert 205 <= samples[-1].bt <= 230
    assert all(s.ror > 0 for s in samples if s.t > tp.t + 5)
