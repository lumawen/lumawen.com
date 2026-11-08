"""Server commands: lumawen-admin create-admin EMAIL | lumawen-admin send-emails [--loop]"""

import argparse
import getpass
import logging
import sys
from datetime import UTC, datetime

from sqlalchemy import select, update

from .auth import normalize_email
from .db import _session_factory
from .mail import run_worker
from .models import EmailToken, User
from .security import hash_password


def create_admin(email: str, password: str | None) -> str:
    """Create a confirmed admin, or make an existing account an admin. Returns what happened."""
    email = normalize_email(email)
    with _session_factory()() as db:
        user = db.scalar(select(User).where(User.email == email))
        created = user is None
        if not password and (created or user.password_hash is None):
            raise SystemExit("This account has no password yet, so give it one.")
        if created:
            user = User(email=email)
            db.add(user)
        user.role = "admin"
        db.flush()
        # Confirm links from earlier sign-ups must not work once the account is confirmed.
        db.execute(
            update(EmailToken)
            .where(EmailToken.user_id == user.id, EmailToken.used_at.is_(None))
            .values(used_at=datetime.now(UTC))
        )
        user.email_verified_at = user.email_verified_at or datetime.now(UTC)
        if password:
            user.password_hash = hash_password(password)
        db.commit()
    return "created" if created else "updated"


def _read_password(from_stdin: bool, required: bool) -> str | None:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n") or None
    first = getpass.getpass(
        "Password (blank keeps the current one): " if not required else "Password: "
    )
    if not first:
        return None
    if getpass.getpass("Again: ") != first:
        raise SystemExit("Passwords don't match.")
    return first


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lumawen-admin")
    sub = parser.add_subparsers(dest="command", required=True)
    p_admin = sub.add_parser("create-admin", help="create or promote an admin account")
    p_admin.add_argument("email")
    p_admin.add_argument(
        "--password-stdin", action="store_true", help="read the password from stdin"
    )
    p_send = sub.add_parser("send-emails", help="deliver queued emails")
    p_send.add_argument("--loop", action="store_true", help="keep running (for pm2)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "create-admin":
        with _session_factory()() as db:
            current = db.scalar(
                select(User.password_hash).where(User.email == normalize_email(args.email))
            )
        password = _read_password(args.password_stdin, required=current is None)
        if password is not None and len(password) < 10:
            raise SystemExit("Use at least 10 characters.")
        print(f"{create_admin(args.email, password)} admin {normalize_email(args.email)}")
    elif args.command == "send-emails":
        run_worker(_session_factory(), loop=args.loop)
