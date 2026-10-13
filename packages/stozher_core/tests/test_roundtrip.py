from hypothesis import given
from hypothesis import strategies as st
from stozher_core.codec import decode, encode
from stozher_core.crypto import derive_keys
from stozher_core.record import EventClass, Extended, MsgType, Osnma, WitnessRecord
from stozher_core.scaling import decode_jump, encode_jump

u8 = st.integers(0, 255)
ext = st.builds(Extended, satellites=u8, pos_jump_code=u8, clock_jump_code=u8, battery=u8)
records = st.builds(
    WitnessRecord,
    type=st.sampled_from(MsgType),
    seq=st.integers(0, 0xFFFF),
    key_id=st.integers(0, 15),
    event_class=st.sampled_from(EventClass),
    confidence=st.integers(0, 31),
    osnma=st.sampled_from(Osnma),
    stationary=st.booleans(),
    rx_jamming=st.booleans(),
    rx_spoofing=st.booleans(),
    tamper=st.booleans(),
    mesh=st.booleans(),
    lat_e7=st.integers(-900_000_000, 900_000_000),
    lon_e7=st.integers(-1_800_000_000, 1_800_000_000),
    timestamp=st.integers(0, 0xFFFFFFFF),
    duration_s=st.integers(0, 0xFFFF),
    cn0_mean=u8,
    cn0_drop=u8,
    cn0_spread=u8,
    agc=u8,
    extended=st.none() | ext,
)


@given(records, st.integers(0, 0xFFFFFFFF), st.binary(min_size=16, max_size=16))
def test_roundtrip(rec: WitnessRecord, node_id: int, master: bytes) -> None:
    keys = derive_keys(master, node_id, rec.key_id)
    raw = encode(rec, node_id, keys)
    assert len(raw) == (32 if rec.extended else 28)
    assert decode(raw, node_id, keys) == rec


def test_jump_scaling_monotonic_and_bounded() -> None:
    codes = [encode_jump(m) for m in (0, 1, 10, 1000, 1e6, 1e12)]
    assert codes == sorted(codes) and codes[0] == 0 and codes[-1] == 255
    assert abs(decode_jump(encode_jump(1000)) - 1000) / 1000 < 0.1
