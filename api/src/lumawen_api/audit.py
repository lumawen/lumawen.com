"""The activity log: every staff action (role changes, suspensions, sends, deletes).

Deleting an account or a subscriber keeps their address in the log for DELETED_DAYS, so staff
can see who was deleted and by whom. After that the hourly worker blanks the address in the
delete row and in that person's earlier rows (purge_deleted_from_log).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import AuditEntry, User

DELETED_DAYS = 30
# The delete actions, and the namespace of rows about the same kind of subject.
_DELETES = {"user.delete": "user", "subscriber.erase": "subscriber"}
ERASED = "(erased)"


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


def erase_from_log(db: Session, email: str, subject: str, until: datetime) -> None:
    """Keep what happened but not who it happened to.

    Only rows about this kind of subject ("user" or "subscriber") up to `until` are touched. An
    account and a list sign-up can share an address, and anyone can create a sign-up for any
    address, so erasing a subscriber must never blank the history of the account with the same
    email; and someone who signs up again later keeps their new history. Who did each action
    (actor_email) is kept on purpose, for accountability.
    """
    db.execute(
        update(AuditEntry)
        .where(
            AuditEntry.target == email,
            AuditEntry.action.startswith(f"{subject}.", autoescape=True),
            AuditEntry.created_at <= until,
        )
        .values(target=ERASED)
    )


def purge_deleted_from_log(db: Session) -> int:
    """Blank the address of anyone deleted more than DELETED_DAYS ago (the worker runs this
    hourly). Returns how many deletes were processed."""
    due = db.execute(
        select(AuditEntry.action, AuditEntry.target, AuditEntry.created_at).where(
            AuditEntry.action.in_(_DELETES),
            AuditEntry.created_at < datetime.now(UTC) - timedelta(days=DELETED_DAYS),
            ~AuditEntry.target.startswith("("),  # already erased, or an old row without one
        )
    ).all()
    for action, email, when in due:
        erase_from_log(db, email, _DELETES[action], until=when)
    db.commit()
    return len(due)
