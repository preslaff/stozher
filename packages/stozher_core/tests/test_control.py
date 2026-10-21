import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from stozher_core.control import (
    ACK_LEN,
    Ack,
    ControlError,
    ack_covers,
    ack_from_text,
    ack_reach,
    ack_to_text,
    private_key_from_seed,
    public_key_from_bytes,
    sign_ack,
    summarize,
    verify_ack,
)

DATA = json.loads((Path(__file__).parents[3] / "tests/vectors/ack_messages.json").read_text())
KEY = private_key_from_seed(bytes.fromhex(DATA["seed_hex"]))
TRUSTED = {DATA["cloud_key_id"]: public_key_from_bytes(bytes.fromhex(DATA["public_key_hex"]))}


def test_public_key_matches_the_independently_derived_one() -> None:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    raw = KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    assert raw.hex() == DATA["public_key_hex"]


@pytest.mark.parametrize("v", DATA["valid"], ids=lambda v: v["name"])
def test_valid_vector(v: dict) -> None:
    ack = Ack(v["cloud"], v["node"], v["key"], v["max_seq"], v["mask"], v["at"])
    signed = sign_ack(ack, KEY)
    assert (
        signed.hex() == v["message_hex"]
    )  # Ed25519 is deterministic: same bytes as openssl produced
    assert len(signed) == ACK_LEN
    assert verify_ack(bytes.fromhex(v["message_hex"]), TRUSTED) == ack
    assert ack_to_text(signed) == v["text"] and ack_from_text(v["text"]) == signed


@pytest.mark.parametrize("v", DATA["invalid"], ids=lambda v: v["name"])
def test_invalid_vector(v: dict) -> None:
    with pytest.raises(ControlError) as err:
        raw = ack_from_text(v["text"]) if v["text"] is not None else bytes.fromhex(v["message_hex"])
        verify_ack(raw, TRUSTED)
    assert err.value.reason.value == v["reason"]


def test_ack_signed_by_another_key_is_rejected() -> None:
    other = private_key_from_seed(bytes(range(1, 33)))
    msg = sign_ack(Ack(1, 1, 0, 5, 0, 1), other)
    with pytest.raises(ControlError) as err:
        verify_ack(msg, TRUSTED)
    assert err.value.reason.value == "bad_signature"


def test_ack_reach_is_the_highest_covered_sequence() -> None:
    assert ack_reach(0, 0) == 0  # nothing acknowledged
    assert ack_reach(3, 0) == 3
    assert ack_reach(2, 0b101) == 6  # bit 2 -> sequence 2 + 2 + 2
    # sequence 1 never arrived: a full mask reaches 17, and nothing beyond it is ever acknowledged
    max_seq, mask = summarize(range(2, 271))
    assert (max_seq, mask, ack_reach(max_seq, mask)) == (0, 0xFFFF, 17)
    assert ack_covers(max_seq, mask, 17) and not ack_covers(max_seq, mask, 18)


@given(st.sets(st.integers(min_value=1, max_value=60)))
def test_ack_reach_matches_covers(received: set[int]) -> None:
    max_seq, mask = summarize(received)
    reach = ack_reach(max_seq, mask)
    assert all(ack_covers(max_seq, mask, s) == (s in received and s <= reach) for s in range(1, 80))


def test_summarize_contiguity_and_mask() -> None:
    assert summarize([]) == (0, 0)
    assert summarize([1, 2, 3]) == (3, 0)
    assert summarize([1, 2, 4]) == (2, 0b1)  # 3 missing; 4 = max_seq + 2 -> bit 0
    assert summarize([1, 2, 4, 6, 20]) == (
        2,
        0b101,
    )  # 4 -> bit 0, 6 -> bit 2, 20 is beyond the window
    assert summarize([0, 1]) == (1, 0)  # sequence 0 is not part of the run
    assert summarize([2, 3]) == (0, 0b11)  # 1 missing: 2 -> bit 0, 3 -> bit 1


def test_covers_matches_summarize() -> None:
    received = {1, 2, 3, 5, 6, 9}
    n, mask = summarize(received)
    ack = Ack(1, 1, 0, n, mask, 0)
    assert all(ack.covers(s) == (s in received) for s in range(1, 30))


@given(st.sets(st.integers(1, 200), max_size=60))
def test_summarize_roundtrips_within_the_window(received: set[int]) -> None:
    n, mask = summarize(received)
    ack = Ack(1, 1, 0, n, mask, 0)
    assert n + 1 not in received  # the contiguous run really ended
    for s in range(1, n + 2 + 16):
        assert ack.covers(s) == (s in received)
