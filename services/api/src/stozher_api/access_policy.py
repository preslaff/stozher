"""The access rules of docs/access.md as pure functions: no database, no I/O.

Credential kinds and the roles they may hold, lifetimes, which actions need two administrators, and who
may approve. The routes and the service layer call these; tests pin every rule.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from .auth import RANK

ORG_TYPES = (
    "platform",
    "rescue_service",
    "municipality",
    "police",
    "media",
    "ngo",
    "authority",
    "research",
    "system_operator",
    "other",
)
KINDS = ("field", "service", "integration", "staff", "break_glass")

# docs/access.md section 6: which roles each kind of credential may hold.
KIND_ROLES: dict[str, frozenset[str]] = {
    "field": frozenset({"public", "reporter", "responder"}),
    "service": frozenset({"system", "responder"}),
    "integration": frozenset({"public", "responder"}),
    "staff": frozenset({"operator", "admin"}),
    "break_glass": frozenset({"admin"}),
}
DEFAULT_LIFETIME = {
    "field": timedelta(days=90),
    "service": timedelta(days=365),
    "integration": timedelta(days=180),
    "staff": timedelta(days=90),
    "break_glass": timedelta(days=365),
}
MAX_LIFETIME = {
    "field": timedelta(days=365),
    "service": timedelta(days=730),
    "integration": timedelta(days=365),
    "staff": timedelta(days=180),
    "break_glass": timedelta(days=730),
}
GATED_ROLES = frozenset({"admin", "operator"})
STAFF_ROLES = ("operator", "admin")
BOOTSTRAP_BELOW = (
    2  # fewer named, active administrators than this: setup mode (single admin, audited)
)


class PolicyError(ValueError):
    """A rule refused the request; the message says which and why."""


def check_kind_role(kind: str, role: str) -> None:
    if kind not in KINDS:
        raise PolicyError(f"kind must be one of {', '.join(KINDS)}")
    if role not in KIND_ROLES[kind]:
        raise PolicyError(f"a {kind} credential may hold: {', '.join(sorted(KIND_ROLES[kind]))}")


def check_organisation(org_type: str, org_status: str, org_max_role: str, role: str) -> None:
    """May this organisation be issued a credential with this role?"""
    if org_status != "active":
        raise PolicyError(
            f"the organisation is {org_status}: credentials are issued only to active organisations"
        )
    if role in GATED_ROLES and org_type != "platform":
        raise PolicyError(
            "operator and admin credentials belong to the platform's own organisation only"
        )
    if role == "system":
        if org_type not in ("platform", "system_operator"):
            raise PolicyError("system credentials belong to the platform or to a system operator")
    elif RANK[role] > RANK[org_max_role]:
        raise PolicyError(f"the organisation is limited to the {org_max_role} role")


def expiry_for(kind: str, requested: datetime | None, now: datetime) -> datetime:
    """Every credential expires. No request means the default for its kind; a longer one than allowed is refused."""
    if requested is None:
        return now + DEFAULT_LIFETIME[kind]
    if requested <= now:
        raise PolicyError("expires_at must be in the future")
    if requested > now + MAX_LIFETIME[kind]:
        raise PolicyError(f"a {kind} credential lasts at most {MAX_LIFETIME[kind].days} days")
    return requested


def key_needs_two_admins(role: str, kind: str) -> bool:
    return role in GATED_ROLES or kind in ("staff", "break_glass")


def is_bootstrap(named_active_admins: int) -> bool:
    return named_active_admins < BOOTSTRAP_BELOW


@dataclass(frozen=True)
class Actor:
    """Who is acting: a named person (their account) or an unbound key (its prefix)."""

    kind: str  # "user" or "key"
    ref: str

    @property
    def named(self) -> bool:
        return self.kind == "user"


def can_approve(requester: Actor, approver: Actor, *, bootstrap: bool) -> None:
    """The second administrator must be a different person; once setup is over, a named one."""
    if bootstrap:
        return  # setup mode: a single administrator may complete the request; the audit log says so
    if not approver.named:
        raise PolicyError("approval needs a named administrator account")
    if approver == requester:
        raise PolicyError("a request must be approved by a different administrator")
