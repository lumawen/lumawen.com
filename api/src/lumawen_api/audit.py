"""The activity log: every staff action (role changes, suspensions, sends, deletes)."""

from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEntry, User


def record(db: Session, actor: User, action: str, target: str, **detail: Any) -> None:
    """Add an entry to the caller's transaction, so it's saved only if the action is."""
    db.add(
        AuditEntry(
            actor_id=actor.id,
            actor_email=actor.email,
            action=action,
            target=target[:400],
            detail=detail,
        )
    )
