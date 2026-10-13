"""Encrypt node master keys at rest with AES-GCM under STOZHER_KEK."""

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class KekMissingError(RuntimeError):
    pass


def _aead(kek_hex: str) -> AESGCM:
    if len(kek_hex) != 64:
        raise KekMissingError("STOZHER_KEK must be 32 bytes as 64 hex characters")
    return AESGCM(bytes.fromhex(kek_hex))


def seal(kek_hex: str, plaintext: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + _aead(kek_hex).encrypt(nonce, plaintext, aad)


def unseal(kek_hex: str, blob: bytes, aad: bytes) -> bytes:
    return _aead(kek_hex).decrypt(blob[:12], blob[12:], aad)
