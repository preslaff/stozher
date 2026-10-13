import json
from pathlib import Path

import pytest
from stozher_core.codec import decode_hex, encode_hex
from stozher_core.crypto import derive_keys
from stozher_core.errors import PacketError
from stozher_core.record import Extended, WitnessRecord

DATA = json.loads((Path(__file__).parents[3] / "tests/vectors/witness_records.json").read_text())

FIELDS = [
    "type",
    "seq",
    "key_id",
    "event_class",
    "confidence",
    "osnma",
    "stationary",
    "rx_jamming",
    "rx_spoofing",
    "tamper",
    "mesh",
    "lat_e7",
    "lon_e7",
    "timestamp",
    "duration_s",
    "cn0_mean",
    "cn0_drop",
    "cn0_spread",
    "agc",
]


def record_from(v: dict) -> WitnessRecord:
    kw = {k: v[k] for k in FIELDS}
    for b in ("stationary", "rx_jamming", "rx_spoofing", "tamper", "mesh"):
        kw[b] = bool(kw[b])
    if v.get("extended"):
        kw["extended"] = Extended(
            satellites=v["satellites"],
            pos_jump_code=v["pos_jump_code"],
            clock_jump_code=v["clock_jump_code"],
            battery=v["battery"],
        )
    return WitnessRecord(**kw)


def keys_for(v: dict):
    return derive_keys(bytes.fromhex(v["master_key"]), v["node_id"], v["key_id"])


@pytest.mark.parametrize("v", DATA["valid"], ids=lambda v: v["name"])
def test_valid_vector(v: dict) -> None:
    rec, keys = record_from(v), keys_for(v)
    assert encode_hex(rec, v["node_id"], keys) == v["hex"]
    assert decode_hex(v["hex"], v["node_id"], keys) == rec
    assert decode_hex(v["hex"].upper(), v["node_id"], keys) == rec


@pytest.mark.parametrize("v", DATA["invalid"], ids=lambda v: v["name"])
def test_invalid_vector(v: dict) -> None:
    with pytest.raises(PacketError) as err:
        decode_hex(v["hex"], v["node_id"], keys_for(v))
    assert err.value.reason.value == v["reason"]
