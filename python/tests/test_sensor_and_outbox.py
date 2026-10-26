"""SCD41 driver failure handling against a fake bus, and outbox durability."""

import pytest

from edgekit.outbox import Outbox
from edgekit.sensirion import Scd41, SensorNotReady, word_with_crc

SEC = 1_000_000_000


class FakeScd41Bus:
    """Behaves like the real sensor, including its unpleasant habits."""

    def __init__(self):
        self.ready = True
        self.co2_raw = 812
        self.asc = 1          # factory default: ON
        self.measuring = False
        self.writes = []

    def write(self, addr, data):
        self.writes.append(bytes(data[:2]))
        cmd = bytes(data[:2])
        if cmd == Scd41.START_PERIODIC:
            self.measuring = True
        elif cmd == Scd41.STOP_PERIODIC:
            self.measuring = False
        elif cmd == Scd41.SET_ASC:
            self.asc = (data[2] << 8) | data[3]

    def write_then_read(self, addr, data, nbytes, delay_s=0.0):
        cmd = bytes(data)
        if cmd == Scd41.DATA_READY:
            return word_with_crc(0x0001 if (self.ready and self.measuring) else 0x8000)
        if cmd == Scd41.GET_ASC:
            return word_with_crc(self.asc)
        if cmd == Scd41.READ_MEASUREMENT:
            if not (self.ready and self.measuring):  # the real sensor answers zeros
                return word_with_crc(0) * 3
            return word_with_crc(self.co2_raw) + word_with_crc(0x6667) + word_with_crc(0x8000)
        raise AssertionError(f"unexpected command {cmd.hex()}")


def sensor():
    bus = FakeScd41Bus()
    return Scd41(bus, sleep=lambda _s: None), bus


def test_reads_and_disables_auto_calibration_by_read_back():
    s, bus = sensor()
    co2, temp, rh = s.read(0)
    assert co2 == 812 and temp == pytest.approx(25.0, abs=0.01) and rh == pytest.approx(50.0)
    assert s.asc_enabled is False and bus.asc == 0


def test_not_ready_is_an_error_not_a_zero():
    s, bus = sensor()
    s.read(0)
    bus.ready = False
    with pytest.raises(SensorNotReady):
        s.read(5 * SEC)


def test_zero_co2_word_is_refused_even_when_ready():
    s, bus = sensor()
    bus.co2_raw = 0
    with pytest.raises(SensorNotReady, match="impossible"):
        s.read(0)


def test_sensor_reset_to_idle_is_recovered_automatically():
    s, bus = sensor()
    s.read(0)
    bus.measuring = False          # brown-out: sensor wakes up idle
    bus.asc = 1                    # ...and back at factory calibration settings
    for t in (5, 10, 20):
        with pytest.raises(SensorNotReady, match="no new sample"):
            s.read(t * SEC)
    with pytest.raises(SensorNotReady, match="re-initialising"):
        s.read(31 * SEC)
    assert s.read(35 * SEC)[0] == 812   # STOP -> ASC off -> START happened again
    assert s.restarts == 1 and bus.asc == 0


def test_outbox_survives_outage_and_replays_in_order(tmp_path):
    box = Outbox(tmp_path / "out.db")
    for i in range(1000):
        box.enqueue("telemetry", {"seq": i}, created_ns=i)
    box.close()
    box = Outbox(tmp_path / "out.db")            # process restart during the outage
    batch = box.pending(limit=1000)
    assert [p["seq"] for _, _, p in batch] == list(range(1000))
    box.mark_sent([i for i, _, _ in batch[:600]])
    assert box.unsent_count() == 400


def test_outbox_ring_buffer_keeps_newest():
    box = Outbox(":memory:")
    for i in range(10):
        box.enqueue("t", {"seq": i}, i)
    assert box.prune_unsent(4) == 6
    assert [p["seq"] for _, _, p in box.pending()] == [6, 7, 8, 9]


def test_outbox_rejects_nan_payloads():
    with pytest.raises(ValueError):
        Outbox(":memory:").enqueue("t", {"co2": float("nan")}, 0)
