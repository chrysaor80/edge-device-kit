"""Lumped-capacitance coffee roaster model — the "physics" behind the simulator.

Gas / airflow / batch weight in, bean temperature (BT), environment temperature (ET)
and rate of rise (RoR) out. It reproduces the qualitative shape of a real roast
(charge -> turning point -> drying -> first crack -> drop) so that protocol tests run
against plausible register values instead of random numbers.

Deterministic on purpose: no randomness, so regression tests are reproducible.
The constants are tuned for behaviour, not for physical accuracy.

Extracted from the BeanPal conformance harness.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, List, NamedTuple, Optional, Union

_AMBIENT_C = 22.0       # room temperature
_CHARGE_TEMP_C = 200.0  # drum preheat at charge
_ET_MAX_C = 430.0       # air temperature ceiling at full gas, closed damper
_AIR_COOL_C = 90.0      # ET drop at 100 % airflow
_TAU_ET_S = 40.0        # air responds quickly
_TAU_BT_S = 420.0       # bean thermal mass lag (250 g reference)
_REF_WEIGHT_G = 250.0
_COLD0 = 2.6            # initial heat sink right after charge (degC/s) -> turning point
_COLD_TAU_S = 50.0
_COLD_AIR_FRAC = 0.3    # share of the charge sink that also cools the air
_DRY_ENDO = -0.04       # endothermic drying phase (degC/s)
_DRY_LO, _DRY_HI = 105.0, 150.0
_FC_EXO = 0.06          # exothermic first crack (degC/s)
_FC_LO, _FC_HI = 196.0, 206.0


class Controls(NamedTuple):
    """Control inputs, each 0..100."""

    gas: float
    airflow: float
    drum: float


class Sample(NamedTuple):
    t: float    # seconds since charge
    bt: float   # bean temperature, degC
    et: float   # environment temperature, degC
    ror: float  # BT rate of rise, degC/min


ControlsInput = Union[Controls, Callable[[float], Controls]]


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v / 100.0))


@dataclass
class RoasterModel:
    """Example::

        samples = RoasterModel(charge_weight_g=300).simulate(Controls(70, 30, 50), 720)
    """

    ambient_c: float = _AMBIENT_C
    charge_temp_c: float = _CHARGE_TEMP_C
    charge_weight_g: float = _REF_WEIGHT_G

    def _et_target(self, c: Controls) -> float:
        return self.ambient_c + _clamp01(c.gas) * (_ET_MAX_C - self.ambient_c) - _clamp01(c.airflow) * _AIR_COOL_C

    @staticmethod
    def _reaction(bt: float) -> float:
        if _DRY_LO <= bt < _DRY_HI:
            return _DRY_ENDO
        if _FC_LO <= bt < _FC_HI:
            return _FC_EXO
        return 0.0

    def simulate(self, controls: ControlsInput, duration_s: float = 720.0, dt: float = 0.5) -> List[Sample]:
        ctrl_fn = controls if callable(controls) else (lambda _t: controls)
        tau_bt = _TAU_BT_S * (self.charge_weight_g / _REF_WEIGHT_G)
        et = bt = prev_bt = self.charge_temp_c
        out: List[Sample] = []
        for i in range(int(round(duration_s / dt)) + 1):
            t = i * dt
            c = ctrl_fn(t)
            cold = _COLD0 * (self.charge_weight_g / _REF_WEIGHT_G) * math.exp(-t / _COLD_TAU_S)
            et_dot = (self._et_target(c) - et) / _TAU_ET_S - _COLD_AIR_FRAC * cold
            bt_dot = (et - bt) / tau_bt - cold + self._reaction(bt)
            if i > 0:
                et += et_dot * dt
                bt += bt_dot * dt
            ror = 0.0 if i == 0 else (bt - prev_bt) / dt * 60.0
            prev_bt = bt
            out.append(Sample(round(t, 3), round(bt, 3), round(et, 3), round(ror, 3)))
        return out


def find_turning_point(samples: List[Sample]) -> Sample:
    """Turning point = BT minimum right after charge."""
    return min(samples, key=lambda s: s.bt)


def find_first_crossing(samples: List[Sample], temp_c: float, after_s: float = 0.0) -> Optional[Sample]:
    for s in samples:
        if s.t >= after_s and s.bt >= temp_c:
            return s
    return None
