"""Receiver observables from simple, documented models. Numbers only, no signals.

Simplifications (assumptions, not RF design): log-distance path loss from a 100 m reference,
the standard effective C/N0 formula for a broadband interferer, and jammer type as a
multiplier on its effect. Good enough to make detection ranges plausible, not to predict them.
"""

import math

FREQ_MHZ = 1575.42
SIGNAL_DBM = -128.5  # received GNSS signal power
CN0_NOMINAL_DBHZ = 44.0
CHIP_RATE = 1.023e6
TYPE_FACTOR = {"wideband": 1.0, "chirp": 1.5, "tone": 0.6}  # effective-bandwidth multiplier


def path_loss_db(distance_m: float, exponent: float) -> float:
    d = max(distance_m, 10.0)
    ref = 32.44 + 20 * math.log10(0.1) + 20 * math.log10(FREQ_MHZ)  # free space at 100 m
    return ref + 10 * exponent * math.log10(d / 100.0)


def interferer_dbm(eirp_dbm: float, distance_m: float, exponent: float) -> float:
    return eirp_dbm - path_loss_db(distance_m, exponent)


def effective_cn0(cn0_dbhz: float, jam_dbm: float | None, kind: str = "wideband") -> float:
    """(C/N0)eff = 1 / (1/(C/N0) + (J/S)/(Q Rc)); no interferer leaves C/N0 unchanged."""
    if jam_dbm is None:
        return cn0_dbhz
    js = 10 ** ((jam_dbm - SIGNAL_DBM) / 10)
    inv = 1 / (10 ** (cn0_dbhz / 10)) + js / (TYPE_FACTOR[kind] * CHIP_RATE)
    return 10 * math.log10(1 / inv)


def agc_level(jam_dbm: float | None) -> int:
    """Receiver AGC/jamming-indicator scale 0..255: quiet baseline ~100, rises with interference."""
    if jam_dbm is None or jam_dbm < -120:
        return 100
    return max(0, min(255, round(100 + 2 * (jam_dbm + 120))))
