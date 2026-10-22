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
    contiguous_run,
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
    assert (
        ack_reach(0, 0xFFFF) == 17
    )  # an ack signed before the distance rule, pinned behind sequence 1
    # sequence 1 never arrived: it is given up, and the ack reaches the newest record
    max_seq, mask = summarize(range(2, 271))
    assert (max_seq, mask, ack_reach(max_seq, mask)) == (270, 0, 270)


@given(st.sets(st.integers(min_value=1, max_value=60)))
def test_ack_reach_matches_covers(received: set[int]) -> None:
    max_seq, mask = summarize(received)
    reach = ack_reach(max_seq, mask)
    assert reach == max(received, default=0)  # the newest record is always acknowledged
    assert all(
        ack_covers(max_seq, mask, s) == (s in received or s <= max_seq) for s in range(1, 80)
    )


def test_contiguous_run_is_exact() -> None:
    assert contiguous_run([]) == 0
    assert contiguous_run([0, 1, 2, 4]) == 2  # sequence 0 is not part of the run
    assert (
        contiguous_run(range(2, 271)) == 0
    )  # sequence 1 never arrived, however far the records go


def test_summarize_contiguity_and_mask() -> None:
    assert summarize([]) == (0, 0)
    assert summarize([1, 2, 3]) == (3, 0)
    assert summarize([1, 2, 4]) == (2, 0b1)  # 3 missing; 4 = max_seq + 2 -> bit 0
    # H = 20: 3 is 17 behind and given up, so max_seq runs on through 4; 6 -> bit 0, 20 -> bit 14
    assert summarize([1, 2, 4, 6, 20]) == (4, 1 | 1 << 14)
    assert summarize([0, 1]) == (1, 0)  # sequence 0 is not part of the run
    assert summarize([2, 3]) == (0, 0b11)  # 1 missing: 2 -> bit 0, 3 -> bit 1


R = range  # the table in docs/control.md, "Giving up on old gaps"


@pytest.mark.parametrize(
    ("held", "expected"),
    [
        ([*R(2, 11)], (0, 0x1FF)),  # within the window: as before
        ([*R(2, 18)], (0, 0xFFFF)),
        ([*R(2, 19)], (18, 0)),  # sequence 1 is now 17 behind: given up
        ([*R(2, 31)], (30, 0)),  # the a4b0 case
        (
            [*R(1, 4), *R(5, 11), *R(12, 15)],
            (3, 0b111_0_111111),
        ),  # 5..10 -> bits 0-5, 12..14 -> bits 7-9
        ([*R(1, 4), *R(5, 41), *R(42, 46)], (40, 0b1111)),  # 4 given up; 41 still inside the window
    ],
)
def test_summarize_gives_up_gaps_17_behind_the_newest(
    held: list[int], expected: tuple[int, int]
) -> None:
    assert summarize(held) == expected


def test_a_late_record_never_lowers_max_seq() -> None:
    held = [*R(1, 4), *R(5, 41), *R(42, 46)]
    assert summarize([*held, 4]) == (40, 0b1111)


def test_covers_matches_summarize() -> None:
    received = {1, 2, 3, 5, 6, 9}
    n, mask = summarize(received)
    ack = Ack(1, 1, 0, n, mask, 0)
    assert all(ack.covers(s) == (s in received) for s in range(1, 30))


@given(st.sets(st.integers(1, 200), max_size=60))
def test_summarize_describes_the_newest_window(received: set[int]) -> None:
    n, mask = summarize(received)
    ack = Ack(1, 1, 0, n, mask, 0)
    high = max(received, default=0)
    assert n >= high - 17 and n >= contiguous_run(received)
    assert (
        n == 0 or n in received or n == high - 17
    )  # the run ended on a received record, or at the horizon
    assert n + 1 not in received  # the run really ended
    for s in range(n + 1, high + 2):
        assert ack.covers(s) == (s in received)  # above max_seq the mask is exact


@given(st.sets(st.integers(1, 200), max_size=60), st.integers(1, 200))
def test_max_seq_never_decreases_as_records_arrive(received: set[int], late: int) -> None:
    assert summarize(received | {late})[0] >= summarize(received)[0]
