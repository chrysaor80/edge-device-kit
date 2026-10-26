"""Virtual serial roaster: a Modbus RTU slave behind a pseudo-terminal (Linux/macOS).

Lets any serial client — the Python reader, the C++ ``edgekit_poll`` tool, or a
third-party program — talk to a "roaster" without hardware or socat.

Registers (holding, x10 fixed point, signed):
    0 = BT (bean temperature)   1 = ET (environment temperature)
    10 = gas setpoint 0..100    (writable with function 0x06)

Run:  python -m edgekit.sim_pty --speed 20
"""

from __future__ import annotations

import argparse
import os
import select
import threading
import time
import tty

from .modbus_rtu import ModbusRtuSlave
from .roaster_model import Controls, RoasterModel

REG_BT, REG_ET, REG_GAS = 0, 1, 10


def _to_reg(value: float) -> int:
    return int(round(value * 10)) & 0xFFFF


class PtyRoaster:
    def __init__(self, slave_id: int = 1, speed: float = 1.0, baud: int = 9600):
        self.slave = ModbusRtuSlave(slave_id, {REG_GAS: 65})
        self.speed = speed
        self.frame_gap_s = 3.5 * 10 / baud
        self.master_fd, slave_fd = os.openpty()
        tty.setraw(slave_fd)
        self.path = os.ttyname(slave_fd)
        self._slave_fd = slave_fd  # keep open so the path stays valid
        self._stop = threading.Event()
        self._t0 = time.monotonic()
        self.requests = 0

    def _update_registers(self) -> None:
        elapsed = (time.monotonic() - self._t0) * self.speed
        gas = self.slave.registers.get(REG_GAS, 65)
        sample = RoasterModel().simulate(Controls(gas, 30, 50), duration_s=min(elapsed, 900.0), dt=1.0)[-1]
        self.slave.registers[REG_BT] = _to_reg(sample.bt)
        self.slave.registers[REG_ET] = _to_reg(sample.et)

    def _read_frame(self) -> bytes:
        buf = bytearray()
        while not self._stop.is_set():
            ready, _, _ = select.select([self.master_fd], [], [], self.frame_gap_s if buf else 0.2)
            if not ready:
                if buf:
                    return bytes(buf)
                continue
            buf.extend(os.read(self.master_fd, 256))
        return b""

    def serve_forever(self) -> None:
        while not self._stop.is_set():
            request = self._read_frame()
            if not request:
                continue
            self.requests += 1
            self._update_registers()
            response = self.slave.handle_request(request)
            if response:
                os.write(self.master_fd, response)

    def start(self) -> "PtyRoaster":
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--slave", type=int, default=1)
    ap.add_argument("--speed", type=float, default=1.0, help="simulated seconds per real second")
    args = ap.parse_args()
    sim = PtyRoaster(args.slave, args.speed).start()
    print(f"virtual roaster on {sim.path} (slave {args.slave}, 9600 8N1) — Ctrl+C to stop", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sim.stop()


if __name__ == "__main__":
    main()
