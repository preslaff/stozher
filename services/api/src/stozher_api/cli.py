"""Local admin CLI until the key-management endpoints exist.

    python -m stozher_api.cli create-key --role system --label simulator
Prints the key once; only its hash is stored.
"""

import argparse
import asyncio

from .auth import ROLES, hash_key, new_key
from .config import get_settings
from .db import make_engine, make_sessionmaker
from .models import ApiKey, AuditLog


async def create_key(role: str, label: str) -> str:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    full, prefix = new_key(settings.env_name)
    async with make_sessionmaker(engine)() as session:
        session.add(
            ApiKey(prefix=prefix, hash=hash_key(full), role=role, label=label, created_by="cli")
        )
        session.add(AuditLog(actor="cli", action="key.create", target=prefix, bootstrap=True))
        await session.commit()
    await engine.dispose()
    return full


def gen_signing_key() -> str:
    """A new Ed25519 seed for STOZHER_ED25519_SEED, and the public key to provision on nodes."""
    import os

    from .signing import load_signer

    seed = os.urandom(32).hex()
    signer = load_signer(seed, 1)
    assert signer is not None
    return f"STOZHER_ED25519_SEED={seed}\npublic_key_hex={signer.public_key_hex}  (flash this on nodes, with cloud_key_id 1)"


def main() -> None:
    ap = argparse.ArgumentParser(prog="stozher_api.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser(
        "gen-signing-key", help="print a new acknowledgement signing seed and its public key"
    )
    c = sub.add_parser("create-key")
    c.add_argument("--role", required=True, choices=sorted(ROLES))
    c.add_argument("--label", required=True)
    args = ap.parse_args()
    if args.cmd == "gen-signing-key":
        print(gen_signing_key())
        return
    print(asyncio.run(create_key(args.role, args.label)))


if __name__ == "__main__":
    main()
