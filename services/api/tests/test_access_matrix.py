"""The access matrix in docs/access.md must match the code, and unauthenticated endpoints are a short, known list."""

from fastapi import FastAPI
from stozher_api.access_matrix import DOC, ROLES, allowed, extract, operations, render
from stozher_api.config import Settings
from stozher_api.main import create_app


def app() -> FastAPI:
    return create_app(Settings(database_url="postgresql+asyncpg://x@/x"))


def test_the_matrix_in_the_document_matches_the_code() -> None:
    documented = extract(DOC.read_text())
    assert documented == render(app()), (
        "docs/access.md is out of date with the endpoints' role requirements. "
        "Decide who may call the new or changed endpoint, then run: "
        "uv run python -m stozher_api.access_matrix --write"
    )


def test_only_a_short_known_list_of_endpoints_needs_no_role() -> None:
    open_ops = {(m, p) for p, m, need in operations(app()) if need in ("open", "key")}
    assert open_ops == {
        ("GET", "/health"),  # liveness and optional modules, no key
        ("GET", "/v1/control/public-key"),  # a public key is public; nodes are provisioned with it
        ("GET", "/v1/me"),  # any valid key may ask about itself
    }, (
        "a new endpoint without a role requirement: give it a role, or add it here deliberately and in docs/access.md"
    )


def test_the_system_role_reaches_only_machine_endpoints() -> None:
    for path, method, need in operations(app()):
        if need not in ("open", "key") and allowed(need, "system"):
            assert path.startswith(
                ("/v1/ingest/", "/v1/acks", "/v1/system/heartbeat", "/v1/sync/")
            ), f"system may call {method} {path}"


def test_every_role_is_a_column_and_the_admin_reaches_everything_a_human_role_can() -> None:
    assert ROLES == ("public", "reporter", "responder", "operator", "admin", "system")
    for _path, _method, need in operations(app()):
        for role in ("public", "reporter", "responder", "operator"):
            if allowed(need, role):
                assert allowed(need, "admin")
