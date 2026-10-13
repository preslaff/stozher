"""One way to write an audit entry, so staff actions always name the person (docs/access.md section 5)."""

import uuid

from .auth import Principal
from .models import AuditLog


def audit(p: Principal, action: str, target: str, *, bootstrap: bool = False) -> AuditLog:
    """An entry for `p`'s action: the key prefix always, and the named account when the key is bound to one."""
    return AuditLog(
        actor=p.prefix,
        actor_user_id=uuid.UUID(p.user_id) if p.user_id else None,
        action=action,
        target=target,
        bootstrap=bootstrap,
    )
