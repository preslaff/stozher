"""The C++ test header is generated from the golden vectors; fail if it is stale."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_firmware_vector_header_is_up_to_date() -> None:
    r = subprocess.run(
        [sys.executable, str(ROOT / "firmware/node/tools/gen_vectors_h.py"), "--check"]
    )
    assert r.returncode == 0, "run `make fw-vectors` and commit the result"
