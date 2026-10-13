"""Key derivation, body encryption and tag for witness records (docs/packet.md)."""

import hmac as _hmac
from dataclasses import dataclass
from hashlib import sha256

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

TAG_LEN = 4


@dataclass(frozen=True)
class KeySet:
    enc: bytes
    mac: bytes


def derive_keys(master: bytes, node_id: int, key_id: int) -> KeySet:
    salt = node_id.to_bytes(4, "big")
    kid = bytes([key_id])

    def one(label: bytes, length: int) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=label + kid).derive(
            master
        )

    return KeySet(enc=one(b"stozher/v1/enc", 16), mac=one(b"stozher/v1/mac", 32))


def crypt(body: bytes, enc_key: bytes, node_id: int, seq: int) -> bytes:
    """AES-128-CTR; symmetric, so it both encrypts and decrypts."""
    counter = node_id.to_bytes(4, "big") + seq.to_bytes(2, "big") + bytes(6) + bytes(4)
    ctx = Cipher(algorithms.AES(enc_key), modes.CTR(counter)).encryptor()
    return ctx.update(body) + ctx.finalize()


def make_tag(mac_key: bytes, node_id: int, header: bytes, ciphertext: bytes) -> bytes:
    msg = node_id.to_bytes(4, "big") + header + ciphertext
    return _hmac.new(mac_key, msg, sha256).digest()[:TAG_LEN]


def tag_ok(mac_key: bytes, node_id: int, header: bytes, ciphertext: bytes, tag: bytes) -> bool:
    return _hmac.compare_digest(make_tag(mac_key, node_id, header, ciphertext), tag)
