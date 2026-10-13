"""Generate ack_messages.json for docs/control.md.

Independent of stozher_core: the Ed25519 signatures come from the `openssl` CLI and the payload is
packed here by hand. The seed is a public test value and must never be used outside tests.
Run: python3 tests/vectors/make_ack_vectors.py
"""

import json
import struct
import subprocess
import tempfile
from pathlib import Path

SEED = bytes(range(32))
PKCS8_PREFIX = bytes.fromhex(
    "302e020100300506032b657004220420"
)  # Ed25519 PrivateKeyInfo with a 32-byte seed
CONTEXT = b"stozher/v1/ack"
CLOUD_KEY_ID = 1


def openssl(args: list[str], data: bytes | None = None) -> bytes:
    return subprocess.run(["openssl", *args], input=data, capture_output=True, check=True).stdout


with tempfile.TemporaryDirectory() as tmp:
    key_path = Path(tmp) / "k.der"
    key_path.write_bytes(PKCS8_PREFIX + SEED)
    public = openssl(
        ["pkey", "-inform", "DER", "-in", str(key_path), "-pubout", "-outform", "DER"]
    )[-32:]

    def sign(payload: bytes) -> bytes:
        msg = Path(tmp) / "m.bin"
        msg.write_bytes(CONTEXT + payload)
        return openssl(
            [
                "pkeyutl",
                "-sign",
                "-rawin",
                "-inkey",
                str(key_path),
                "-keyform",
                "DER",
                "-in",
                str(msg),
            ]
        )

    def payload(
        cloud: int, node: int, key: int, max_seq: int, mask: int, at: int, type_byte: int = 0xA1
    ) -> bytes:
        return struct.pack(">BBIBHHI", type_byte, cloud, node, key, max_seq, mask, at)

    cases = {
        "ack_basic": dict(cloud=CLOUD_KEY_ID, node=1, key=0, max_seq=5, mask=0, at=1_795_860_000),
        "ack_with_selective_mask": dict(
            cloud=CLOUD_KEY_ID,
            node=115,
            key=2,
            max_seq=10,
            mask=0b1000_0000_0000_0101,
            at=1_795_860_123,
        ),
        "ack_nothing_received_yet": dict(
            cloud=CLOUD_KEY_ID, node=7, key=0, max_seq=0, mask=0, at=1_795_860_001
        ),
        "ack_max_values": dict(
            cloud=CLOUD_KEY_ID, node=0xFFFFFFFF, key=15, max_seq=65535, mask=0xFFFF, at=0xFFFFFFFF
        ),
        "ack_min_values": dict(cloud=CLOUD_KEY_ID, node=1, key=0, max_seq=0, mask=0, at=0),
    }
    out: dict = {
        "description": "Golden vectors for docs/control.md (Ed25519 via openssl). Test seed only.",
        "cloud_key_id": CLOUD_KEY_ID,
        "seed_hex": SEED.hex(),
        "public_key_hex": public.hex(),
        "valid": [],
        "invalid": [],
    }
    good: dict[str, bytes] = {}
    for name, c in cases.items():
        p = payload(c["cloud"], c["node"], c["key"], c["max_seq"], c["mask"], c["at"])
        msg = p + sign(p)
        good[name] = msg
        out["valid"].append(
            {
                "name": name,
                **{k: v for k, v in c.items()},
                "message_hex": msg.hex(),
                "text": "STZA1:" + msg.hex(),
            }
        )

    base = good["ack_basic"]

    def bad(
        name: str, reason: str, message_hex: str | None = None, text: str | None = None
    ) -> None:
        out["invalid"].append(
            {"name": name, "reason": reason, "message_hex": message_hex, "text": text}
        )

    flip = lambda b, i: b[:i] + bytes([b[i] ^ 1]) + b[i + 1 :]  # noqa: E731
    bad("bad_signature_flipped_signature_bit", "bad_signature", flip(base, 40).hex())
    bad("bad_signature_flipped_node_id", "bad_signature", flip(base, 5).hex())
    bad("bad_signature_flipped_max_seq", "bad_signature", flip(base, 8).hex())
    bad("bad_signature_flipped_mask", "bad_signature", flip(base, 10).hex())
    bad("bad_signature_flipped_signed_at", "bad_signature", flip(base, 14).hex())
    p = payload(CLOUD_KEY_ID, 1, 0, 5, 0, 1_795_860_000)
    raw_msg = Path(tmp) / "raw.bin"
    raw_msg.write_bytes(p)  # same payload, signed WITHOUT the context string
    sig_no_context = openssl(
        [
            "pkeyutl",
            "-sign",
            "-rawin",
            "-inkey",
            str(key_path),
            "-keyform",
            "DER",
            "-in",
            str(raw_msg),
        ]
    )
    bad("bad_signature_missing_context_string", "bad_signature", (p + sig_no_context).hex())
    bad(
        "unknown_cloud_key",
        "unknown_cloud_key",
        (payload(9, 1, 0, 5, 0, 1_795_860_000) + base[15:]).hex(),
    )
    bad("bad_type_wrong_version", "bad_type", (bytes([0xA2]) + base[1:]).hex())
    bad("bad_length_truncated", "bad_length", base[:-1].hex())
    bad("bad_length_extended", "bad_length", (base + b"\x00").hex())
    bad("bad_length_empty", "bad_length", "")
    bad("bad_text_wrong_prefix", "bad_text", text="STZ1:" + base.hex())
    bad("bad_hex_in_text", "bad_hex", text="STZA1:zz" + base.hex()[2:])

Path(__file__).with_name("ack_messages.json").write_text(json.dumps(out, indent=2) + "\n")
print(len(out["valid"]), "valid,", len(out["invalid"]), "invalid")
