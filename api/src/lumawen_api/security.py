"""Password hashing (Argon2) and random tokens (stored only as SHA-256 hashes)."""

import hashlib
import secrets
import threading
from collections.abc import Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()
# Checked when an email has no password, so unknown accounts take as long as wrong passwords.
_DUMMY_HASH = _hasher.hash("lumawen-timing-dummy")


# Each Argon2 hash uses 64 MiB. Without a cap, a flood of sign-ups or sign-ins could run dozens
# at once per worker and run the server out of memory; extra requests wait their turn instead.
_slots = threading.BoundedSemaphore(4)
# How long a request waits for a slot before giving up with 503. Without a limit, a login flood
# leaves dozens of request threads parked here and every other endpoint stalls behind them.
HASH_WAIT_SECONDS = 1.0


class HashingBusy(Exception):
    """Every Argon2 slot stayed busy; the app answers 503 with Retry-After."""


def _with_slot[T](fn: Callable[..., T], *args: str) -> T:
    if not _slots.acquire(timeout=HASH_WAIT_SECONDS):
        raise HashingBusy
    try:
        return fn(*args)
    finally:
        _slots.release()


def hash_password(password: str) -> str:
    return _with_slot(_hasher.hash, password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        ok = _with_slot(_hasher.verify, password_hash or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return ok and password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    """A fresh random token and its hash. Only the hash is ever stored."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)
