"""The cloud's Ed25519 signing key, loaded from the environment (never logged or returned)."""

import logging
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from stozher_core.control import private_key_from_seed

log = logging.getLogger("stozher.signing")


@dataclass(frozen=True)
class Signer:
    cloud_key_id: int
    key: Ed25519PrivateKey

    @property
    def public_key_hex(self) -> str:
        return self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def load_signer(seed_hex: str, cloud_key_id: int) -> Signer | None:
    """None when no key is configured: acknowledgements are then disabled, nothing else changes."""
    if not seed_hex:
        return None
    try:
        seed = bytes.fromhex(seed_hex)
        if len(seed) != 32:
            raise ValueError("seed must be 32 bytes")
        return Signer(cloud_key_id, private_key_from_seed(seed))
    except ValueError as exc:
        log.error("STOZHER_ED25519_SEED is invalid (%s); acknowledgements are disabled", exc)
        return None
