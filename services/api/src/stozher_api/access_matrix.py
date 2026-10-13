"""Who may call what: the access matrix, generated from the code (docs/access.md).

Every route's role requirement is read from its dependencies, so the matrix in the document cannot
drift from the behaviour: a test regenerates it and fails when they differ. Adding or changing an
endpoint therefore forces a conscious access decision and a documentation update.

    uv run python -m stozher_api.access_matrix --write    # refresh docs/access.md
"""

import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from .auth import RANK, get_principal

ROLES = ("public", "reporter", "responder", "operator", "admin", "system")
START, END = "<!-- access-matrix:start -->", "<!-- access-matrix:end -->"
DOC = Path(__file__).resolve().parents[4] / "docs" / "access.md"


def _need(dependant: Any) -> str | None:
    """'open' (no key), 'key' (any valid key), or the requirement of the strictest dependency."""
    found: tuple[tuple[str, ...], str | None] | None = None
    uses_key = False
    stack = [dependant]
    while stack:
        d = stack.pop()
        for sub in d.dependencies:
            access = getattr(sub.call, "access", None)
            if access is not None:
                found = access
            if sub.call is get_principal:
                uses_key = True
            stack.append(sub)
    if found is not None:
        allowed, minimum = found
        return f"min:{minimum}" if minimum else "only:" + ",".join(allowed)
    return "key" if uses_key else "open"


def operations(app: FastAPI) -> list[tuple[str, str, str]]:
    flat: list[Any] = []
    for top in app.routes:
        flat.extend(top.original_router.routes if hasattr(top, "original_router") else [top])
    out: list[tuple[str, str, str]] = []
    for r in flat:
        dependant = getattr(r, "dependant", None)
        if dependant is None or not (r.path.startswith("/v1") or r.path == "/health"):
            continue
        need = _need(dependant)
        for method in sorted(r.methods - {"HEAD", "OPTIONS"}):
            out.append((r.path, method, need or "open"))
    return sorted(out)


def allowed(need: str, role: str) -> bool:
    if need == "open":
        return True
    if need == "key":
        return True
    if need.startswith("only:"):
        return role in need[5:].split(",")
    return role in RANK and RANK[role] >= RANK[need[4:]]


def render(app: FastAPI) -> str:
    head = "| Operation | " + " | ".join(ROLES) + " |\n|---|" + "---|" * len(ROLES)
    rows = []
    for path, method, need in operations(app):
        label = "no key" if need == "open" else ""
        cells = [("x" if allowed(need, r) else "") for r in ROLES]
        rows.append(
            f"| `{method} {path}`{' (' + label + ')' if label else ''} | "
            + " | ".join(cells)
            + " |"
        )
    return head + "\n" + "\n".join(rows)


def extract(text: str) -> str:
    a, b = text.index(START) + len(START), text.index(END)
    return text[a:b].strip("\n")


def main() -> None:
    from .config import Settings
    from .main import create_app

    table = render(create_app(Settings(database_url="postgresql+asyncpg://x@/x")))
    if "--write" in sys.argv:
        text = DOC.read_text()
        a, b = text.index(START) + len(START), text.index(END)
        DOC.write_text(text[:a] + "\n" + table + "\n" + text[b:])
        print(f"updated {DOC}")
    else:
        print(table)


if __name__ == "__main__":
    main()
