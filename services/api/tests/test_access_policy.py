from datetime import UTC, datetime, timedelta

import pytest
from stozher_api.access_policy import (
    DEFAULT_LIFETIME,
    KIND_ROLES,
    KINDS,
    Actor,
    PolicyError,
    can_approve,
    check_kind_role,
    check_organisation,
    expiry_for,
    is_bootstrap,
    key_needs_two_admins,
)

NOW = datetime(2026, 11, 28, 10, 0, tzinfo=UTC)


def test_every_kind_has_roles_a_default_and_a_maximum() -> None:
    assert set(KIND_ROLES) == set(KINDS) == set(DEFAULT_LIFETIME)


@pytest.mark.parametrize(
    ("kind", "role", "ok"),
    [
        ("field", "reporter", True),
        ("field", "responder", True),
        ("field", "operator", False),
        ("field", "system", False),
        ("service", "system", True),
        ("service", "reporter", False),
        ("integration", "public", True),
        ("integration", "responder", True),
        ("integration", "admin", False),
        ("staff", "operator", True),
        ("staff", "admin", True),
        ("staff", "responder", False),
        ("break_glass", "admin", True),
        ("break_glass", "operator", False),
    ],
)
def test_kind_and_role_pairs(kind: str, role: str, ok: bool) -> None:
    if ok:
        check_kind_role(kind, role)
    else:
        with pytest.raises(PolicyError):
            check_kind_role(kind, role)
    with pytest.raises(PolicyError):
        check_kind_role("magic", "public")


def test_an_organisation_must_be_active_and_may_not_exceed_its_cap() -> None:
    check_organisation("rescue_service", "active", "responder", "reporter")
    check_organisation("rescue_service", "active", "responder", "responder")
    for status in ("proposed", "suspended", "offboarded"):
        with pytest.raises(PolicyError, match=status):
            check_organisation("rescue_service", status, "responder", "reporter")
    with pytest.raises(PolicyError, match="limited to the reporter"):
        check_organisation("ngo", "active", "reporter", "responder")
    check_organisation("ngo", "active", "responder", "public")  # below the cap is fine


def test_only_the_platform_itself_holds_operator_and_admin() -> None:
    check_organisation("platform", "active", "admin", "operator")
    check_organisation("platform", "active", "admin", "admin")
    for org_type in ("police", "media", "authority", "ngo", "system_operator"):
        with pytest.raises(PolicyError, match="platform's own"):
            check_organisation(org_type, "active", "admin", "operator")


def test_system_credentials_belong_to_the_platform_or_a_system_operator() -> None:
    check_organisation("platform", "active", "responder", "system")
    check_organisation("system_operator", "active", "responder", "system")
    with pytest.raises(PolicyError, match="system"):
        check_organisation("police", "active", "responder", "system")


def test_every_credential_expires() -> None:
    for kind in KINDS:
        assert expiry_for(kind, None, NOW) == NOW + DEFAULT_LIFETIME[kind]
    assert expiry_for("field", NOW + timedelta(days=30), NOW) == NOW + timedelta(days=30)
    with pytest.raises(PolicyError, match="future"):
        expiry_for("field", NOW - timedelta(days=1), NOW)
    with pytest.raises(PolicyError, match="at most"):
        expiry_for("field", NOW + timedelta(days=400), NOW)
    with pytest.raises(PolicyError, match="at most 180"):
        expiry_for("staff", NOW + timedelta(days=181), NOW)


def test_what_needs_two_administrators() -> None:
    assert key_needs_two_admins("admin", "staff") and key_needs_two_admins("operator", "staff")
    assert key_needs_two_admins("admin", "break_glass")
    assert key_needs_two_admins("operator", "field")  # the role alone is enough
    for role, kind in (
        ("reporter", "field"),
        ("responder", "field"),
        ("system", "service"),
        ("public", "integration"),
    ):
        assert not key_needs_two_admins(role, kind)


def test_setup_mode_ends_with_two_named_administrators() -> None:
    assert is_bootstrap(0) and is_bootstrap(1) and not is_bootstrap(2) and not is_bootstrap(5)


def test_the_second_administrator_must_be_a_different_named_person() -> None:
    alice, bob = Actor("user", "alice-id"), Actor("user", "bob-id")
    can_approve(alice, bob, bootstrap=False)
    with pytest.raises(PolicyError, match="different"):
        can_approve(alice, alice, bootstrap=False)
    with pytest.raises(PolicyError, match="named"):
        can_approve(
            alice, Actor("key", "abcd1234"), bootstrap=False
        )  # an unbound key cannot be the second pair of eyes
    can_approve(
        Actor("key", "abcd1234"), bob, bootstrap=False
    )  # a legacy key may ask; a named admin approves
    can_approve(alice, alice, bootstrap=True)  # setup mode: one administrator, audited as such
    can_approve(Actor("key", "abcd1234"), Actor("key", "abcd1234"), bootstrap=True)
