"""The activity log: every staff action (role changes, suspensions, sends, deletes)."""

from typing import Any

from sqlalchemy import update
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


def erase_from_log(db: Session, email: str, subject: str) -> None:
    """On an erase, keep what happened but not who it happened to.

    Only rows about this kind of subject ("user" or "subscriber") are touched. An account and a
    list sign-up can share an address, and anyone can create a sign-up for any address, so
    erasing a subscriber must never blank the history of the account with the same email.
    Who did each action (actor_email) is kept on purpose, for accountability.
    """
    db.execute(
        update(AuditEntry)
        .where(
            AuditEntry.target == email,
            AuditEntry.action.startswith(f"{subject}.", autoescape=True),
        )
        .values(target="(erased)")
    )
