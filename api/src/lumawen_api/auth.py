"""Email accounts: register, confirm email, sign in and out, refresh, reset password.

Web browsers get an HttpOnly cookie. The Android/desktop app gets a short access token
(Authorization: Bearer ...) and a refresh token that rotates on every use.

Lock order, to avoid deadlocks: users row first, then email_tokens / auth_sessions rows.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import AfterValidator, BaseModel, EmailStr, Field
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_session
from .mail import queue_email, recent_count
from .models import AuthSession, EmailToken, LoginThrottle, User
from .security import hash_password, hash_token, needs_rehash, new_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "lumawen_session"
VERIFY_HOURS = 24
RESET_MINUTES = 60
# A refresh token used again this soon after rotating is a retry or a race, not theft.
REFRESH_GRACE = timedelta(seconds=30)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

DB = Annotated[Session, Depends(get_session)]
Password = Annotated[str, Field(min_length=10, max_length=256)]


def _ascii_only(email: str) -> str:
    if not email.isascii():
        raise ValueError("Use an email address with plain (ASCII) letters.")
    return email


Email = Annotated[EmailStr, AfterValidator(_ascii_only)]


def _now() -> datetime:
    return datetime.now(UTC)


def normalize_email(email: str) -> str:
    return email.strip().lower()


# ---- request and response bodies -------------------------------------------------------------


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    role: str
    email_verified: bool
    created_at: datetime

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            email_verified=user.email_verified_at is not None,
            created_at=user.created_at,
        )


class RegisterIn(BaseModel):
    email: Email
    password: Password


class EmailIn(BaseModel):
    email: Email


class ConfirmIn(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=256)  # the one chosen at sign-up


class ResetIn(BaseModel):
    token: str = Field(max_length=200)
    password: Password


class LoginIn(BaseModel):
    email: Email
    password: str = Field(max_length=256)
    client: Literal["web", "app"] = "web"
    device_name: str | None = Field(None, max_length=100)


class RefreshIn(BaseModel):
    refresh_token: str = Field(max_length=200)


class LoginOut(BaseModel):
    user: UserOut
    # Only for client="app"; web sign-in uses the cookie instead.
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str | None = None
    expires_in: int | None = None


class Status(BaseModel):
    status: str


CHECK_EMAIL = Status(status="check_email")
BAD_LINK = "This link is invalid or has expired."


# ---- emails ----------------------------------------------------------------------------------


def _issue_email_token(
    db: Session, user: User, purpose: str, lifetime: timedelta, pending_password: str | None = None
) -> str:
    raw, digest = new_token()
    db.add(
        EmailToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=digest,
            pending_password_hash=pending_password,
            expires_at=_now() + lifetime,
        )
    )
    return raw


def _expire_tokens(db: Session, user: User, purpose: str) -> None:
    db.execute(
        update(EmailToken)
        .where(
            EmailToken.user_id == user.id,
            EmailToken.purpose == purpose,
            EmailToken.used_at.is_(None),
        )
        .values(used_at=_now())
    )


def _send_confirm(db: Session, user: User, pending_password_hash: str) -> None:
    # Each sign-up gets its own link carrying its own password. Confirming needs both the
    # link (proves the inbox) and that password (proves who signed up), so a stranger who
    # signs up with your address first can never end up owning the account.
    if recent_count(db, user.email, "confirm") >= get_settings().emails_per_hour:
        return
    raw = _issue_email_token(
        db, user, "verify", timedelta(hours=VERIFY_HOURS), pending_password_hash
    )
    link = f"{get_settings().dashboard_url}/confirm?token={raw}"
    queue_email(
        db,
        user.email,
        "Confirm your Lumawen account",
        "Hi,\n\n"
        "Confirm your email to finish creating your Lumawen account. Open this link and enter "
        "the password you just chose:\n\n"
        f"{link}\n\n"
        f"The link works for {VERIFY_HOURS} hours. If you didn't sign up, ignore this email "
        "and no account will be created.\n\n"
        "Lumawen\nhttps://lumawen.com\n",
        "confirm",
    )


def _send_reset(db: Session, user: User) -> None:
    if recent_count(db, user.email, "reset") >= get_settings().emails_per_hour:
        return
    _expire_tokens(db, user, "reset")  # only the newest reset link works
    raw = _issue_email_token(db, user, "reset", timedelta(minutes=RESET_MINUTES))
    link = f"{get_settings().dashboard_url}/reset?token={raw}"
    queue_email(
        db,
        user.email,
        "Reset your Lumawen password",
        "Hi,\n\n"
        "Someone (hopefully you) asked to reset the password for your Lumawen account. "
        "Choose a new one here:\n\n"
        f"{link}\n\n"
        f"The link works for {RESET_MINUTES} minutes and only once. If you didn't ask, ignore "
        "this email and your password stays the same.\n\n"
        "Lumawen\nhttps://lumawen.com\n",
        "reset",
    )


def _send_already_registered(db: Session, user: User) -> None:
    queue_email(
        db,
        user.email,
        "You already have a Lumawen account",
        "Hi,\n\n"
        "Someone tried to create a Lumawen account with this email, but you already have one. "
        "If it was you, sign in, or reset your password here:\n\n"
        f"{get_settings().dashboard_url}/forgot\n\n"
        "If it wasn't you, you can ignore this email.\n\n"
        "Lumawen\nhttps://lumawen.com\n",
        "already_registered",
    )


def _lock_email_token(db: Session, raw: str, purpose: str) -> tuple[EmailToken, User]:
    """Find a live one-time token and lock its user, then the token (users first)."""
    digest = hash_token(raw)
    token = db.scalar(
        select(EmailToken).where(EmailToken.token_hash == digest, EmailToken.purpose == purpose)
    )
    if token is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, BAD_LINK)
    user = db.get(User, token.user_id, with_for_update=True)
    db.refresh(token, with_for_update=True)
    if token.used_at is not None or token.expires_at <= _now():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, BAD_LINK)
    return token, user


# ---- sign-in throttling ----------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    # Behind nginx, uvicorn runs with --proxy-headers so this is the visitor's address.
    return request.client.host if request.client else "unknown"


def _throttle_rules(email: str, ip: str) -> list[tuple[str, int, timedelta]]:
    s = get_settings()
    return [
        (f"ip:{ip}|{email}", s.max_failed_logins, timedelta(minutes=s.lockout_minutes)),
        (f"email:{email}", s.max_failed_per_email, timedelta(minutes=s.email_lockout_minutes)),
    ]


def _throttled(db: Session, rules: list[tuple[str, int, timedelta]]) -> bool:
    keys = [key for key, _, _ in rules]
    locks = db.scalars(select(LoginThrottle.locked_until).where(LoginThrottle.key.in_(keys)))
    return any(until is not None and until > _now() for until in locks)


def _record_failure(db: Session, rules: list[tuple[str, int, timedelta]]) -> None:
    now = _now()
    for key, limit, window in rules:
        db.execute(
            pg_insert(LoginThrottle)
            .values(key=key, failures=0, window_started_at=now)
            .on_conflict_do_nothing(index_elements=["key"])
        )
        row = db.get(LoginThrottle, key, with_for_update=True, populate_existing=True)
        if row.window_started_at <= now - window:
            row.failures = 0
            row.window_started_at = now
        row.failures += 1
        if row.failures >= limit:
            row.locked_until = now + window
            row.failures = 0
            row.window_started_at = now


def _clear_failures(db: Session, rules: list[tuple[str, int, timedelta]]) -> None:
    db.execute(delete(LoginThrottle).where(LoginThrottle.key == rules[0][0]))


# ---- sessions --------------------------------------------------------------------------------


def _start_session(db: Session, user: User, client: str, device_name: str | None) -> dict:
    settings = get_settings()
    raw, digest = new_token()
    session = AuthSession(
        user_id=user.id,
        client=client,
        device_name=device_name,
        token_hash=digest,
        last_used_at=_now(),
        expires_at=_now() + timedelta(days=settings.session_days),
    )
    tokens = {"session": raw}
    if client == "app":
        tokens.update(_new_access(session))
    db.add(session)
    return tokens


def _new_access(session: AuthSession) -> dict:
    minutes = get_settings().access_minutes
    raw, digest = new_token()
    session.access_hash = digest
    session.access_expires_at = _now() + timedelta(minutes=minutes)
    return {"access": raw, "expires_in": minutes * 60}


def _set_cookie(response: Response, raw: str) -> None:
    settings = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        raw,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )


def _revoke_all(db: Session, user: User) -> None:
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


def _live(session: AuthSession | None) -> bool:
    return session is not None and session.revoked_at is None and session.expires_at > _now()


def _same_origin(request: Request) -> bool:
    """Block cross-site posts that ride on the cookie (CSRF)."""
    origin = request.headers.get("origin")
    if origin is None:
        return True  # not a cross-site browser request; SameSite=Lax covers the rest
    return origin.rstrip("/") == get_settings().public_base_url.rstrip("/")


def current_session(request: Request, db: DB) -> AuthSession:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in.")
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        digest = hash_token(header[7:].strip())
        session = db.scalar(select(AuthSession).where(AuthSession.access_hash == digest))
        if (
            not _live(session)
            or session.access_expires_at is None
            or session.access_expires_at <= _now()
        ):
            raise unauthorized
    else:
        raw = request.cookies.get(COOKIE_NAME)
        if not raw:
            raise unauthorized
        session = db.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == hash_token(raw), AuthSession.client == "web"
            )
        )
        if not _live(session):
            raise unauthorized
        if request.method not in SAFE_METHODS and not _same_origin(request):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-site request blocked.")
    session.last_used_at = _now()
    db.commit()
    return session


CurrentSession = Annotated[AuthSession, Depends(current_session)]


def current_user(session: CurrentSession) -> User:
    return session.user


CurrentUser = Annotated[User, Depends(current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only.")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


# ---- routes ----------------------------------------------------------------------------------


@router.post("/register", status_code=202, response_model=Status)
def register(body: RegisterIn, db: DB) -> Status:
    """Always answers the same way (and hashes once), so nobody can test who has an account."""
    email = normalize_email(body.email)
    pending = hash_password(body.password)
    db.execute(
        pg_insert(User)
        .values(id=uuid.uuid4(), email=email, role="user")
        .on_conflict_do_nothing(index_elements=["email"])
    )
    user = db.scalar(select(User).where(User.email == email).with_for_update())
    if user.email_verified_at is None:
        _send_confirm(db, user, pending)
    else:
        _send_already_registered(db, user)
    db.commit()
    return CHECK_EMAIL


@router.post("/confirm", response_model=Status)
def confirm_email(body: ConfirmIn, db: DB) -> Status:
    token, user = _lock_email_token(db, body.token, "verify")
    if user.email_verified_at is None:
        if not verify_password(token.pending_password_hash, body.password):
            db.rollback()
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "That isn't the password chosen for this sign-up. Try again, or sign up again.",
            )
        user.email_verified_at = _now()
        user.password_hash = token.pending_password_hash
        _expire_tokens(db, user, "verify")
    token.used_at = _now()
    db.commit()
    return Status(status="confirmed")


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, request: Request, response: Response, db: DB) -> LoginOut:
    if body.client == "web" and not _same_origin(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-site request blocked.")
    email = normalize_email(body.email)
    rules = _throttle_rules(email, _client_ip(request))
    if _throttled(db, rules):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many wrong passwords. Try again later or reset your password.",
        )
    user = db.scalar(select(User).where(User.email == email))
    # Exactly one Argon2 check on every path: the real hash, the newest unconfirmed sign-up's
    # hash, or a dummy for unknown emails, so timing doesn't reveal anything either.
    check = user.password_hash if user is not None else None
    if user is not None and check is None:
        check = _latest_pending_hash(db, user)
    if not verify_password(check, body.password):
        _record_failure(db, rules)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password.")
    if user.password_hash is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Confirm your email first. Check your inbox for the link."
        )

    _clear_failures(db, rules)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    tokens = _start_session(db, user, body.client, body.device_name)
    db.commit()
    if body.client == "web":
        _set_cookie(response, tokens["session"])
        return LoginOut(user=UserOut.of(user))
    return LoginOut(
        user=UserOut.of(user),
        access_token=tokens["access"],
        refresh_token=tokens["session"],
        token_type="bearer",
        expires_in=tokens["expires_in"],
    )


def _latest_pending_hash(db: Session, user: User) -> str | None:
    return db.scalar(
        select(EmailToken.pending_password_hash)
        .where(
            EmailToken.user_id == user.id,
            EmailToken.purpose == "verify",
            EmailToken.used_at.is_(None),
            EmailToken.expires_at > _now(),
        )
        .order_by(EmailToken.created_at.desc())
        .limit(1)
    )


@router.post("/refresh", response_model=LoginOut)
def refresh(body: RefreshIn, db: DB) -> LoginOut:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in again.")
    digest = hash_token(body.refresh_token)
    session = db.scalar(
        select(AuthSession)
        .where(AuthSession.token_hash == digest, AuthSession.client == "app")
        .with_for_update(of=AuthSession)
    )
    if session is None:
        reused = db.scalar(
            select(AuthSession)
            .where(AuthSession.previous_token_hash == digest)
            .with_for_update(of=AuthSession)
        )
        if reused is not None and reused.revoked_at is None:
            if reused.rotated_at is not None and _now() - reused.rotated_at <= REFRESH_GRACE:
                # Two refreshes raced, or a retry: keep the session, use the newest tokens.
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "This refresh token was just used; use the new one."
                )
            # An old refresh token came back much later: probably stolen, so end the session.
            reused.revoked_at = _now()
            db.commit()
        raise unauthorized
    if not _live(session):
        raise unauthorized
    raw, new_digest = new_token()
    session.previous_token_hash = session.token_hash
    session.token_hash = new_digest
    session.rotated_at = _now()
    session.last_used_at = _now()
    access = _new_access(session)
    db.commit()
    return LoginOut(
        user=UserOut.of(session.user),
        access_token=access["access"],
        refresh_token=raw,
        token_type="bearer",
        expires_in=access["expires_in"],
    )


@router.post("/logout", response_model=Status)
def logout(session: CurrentSession, response: Response, db: DB) -> Status:
    session.revoked_at = _now()
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return Status(status="signed_out")


@router.post("/logout-all", response_model=Status)
def logout_all(session: CurrentSession, response: Response, db: DB) -> Status:
    _revoke_all(db, session.user)
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return Status(status="signed_out")


@router.post("/forgot-password", status_code=202, response_model=Status)
def forgot_password(body: EmailIn, db: DB) -> Status:
    user = db.scalar(
        select(User).where(User.email == normalize_email(body.email)).with_for_update()
    )
    if user is not None:
        _send_reset(db, user)
    db.commit()
    return CHECK_EMAIL


@router.post("/reset-password", response_model=Status)
def reset_password(body: ResetIn, db: DB) -> Status:
    token, user = _lock_email_token(db, body.token, "reset")
    user.password_hash = hash_password(body.password)
    user.email_verified_at = user.email_verified_at or _now()  # they proved they get the mail
    _expire_tokens(db, user, "reset")
    _expire_tokens(db, user, "verify")
    _revoke_all(db, user)  # sign out every device
    db.execute(delete(LoginThrottle).where(LoginThrottle.key == f"email:{user.email}"))
    db.commit()
    return Status(status="password_changed")


me_router = APIRouter(tags=["auth"])


@me_router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut.of(user)
