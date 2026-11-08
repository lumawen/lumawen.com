"""Email accounts: register, confirm email, sign in and out, refresh, reset password.

Web browsers get an HttpOnly cookie. The Android/desktop app gets a short access token
(Authorization: Bearer ...) and a refresh token that rotates on every use.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_session
from .mail import queue_email, recent_count
from .models import AuthSession, EmailToken, User
from .security import hash_password, hash_token, needs_rehash, new_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "lumawen_session"
VERIFY_HOURS = 24
RESET_MINUTES = 60
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

DB = Annotated[Session, Depends(get_session)]
Password = Annotated[str, Field(min_length=10, max_length=256)]


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
    email: EmailStr
    password: Password
    display_name: str | None = Field(None, max_length=80)


class EmailIn(BaseModel):
    email: EmailStr


class TokenIn(BaseModel):
    token: str = Field(max_length=200)


class ResetIn(BaseModel):
    token: str = Field(max_length=200)
    password: Password


class LoginIn(BaseModel):
    email: EmailStr
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


def _send_confirm(db: Session, user: User, pending_password_hash: str | None) -> None:
    if recent_count(db, user.email, "confirm") >= get_settings().emails_per_hour:
        return
    # A new sign-up replaces any older confirm links, so only the newest password can win.
    _expire_tokens(db, user, "verify")
    raw = _issue_email_token(
        db, user, "verify", timedelta(hours=VERIFY_HOURS), pending_password_hash
    )
    link = f"{get_settings().dashboard_url}/confirm?token={raw}"
    queue_email(
        db,
        user.email,
        "Confirm your Lumawen account",
        "Hi,\n\n"
        "Confirm your email to finish creating your Lumawen account:\n\n"
        f"{link}\n\n"
        f"The link works for {VERIFY_HOURS} hours. If you didn't sign up, ignore this email "
        "and no account will be created.\n\n"
        "Lumawen\nhttps://lumawen.com\n",
        "confirm",
    )


def _send_reset(db: Session, user: User) -> None:
    if recent_count(db, user.email, "reset") >= get_settings().emails_per_hour:
        return
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


def _use_email_token(db: Session, raw: str, purpose: str) -> EmailToken:
    token = db.scalar(
        select(EmailToken)
        .where(EmailToken.token_hash == hash_token(raw), EmailToken.purpose == purpose)
        .with_for_update()
    )
    if token is None or token.used_at is not None or token.expires_at <= _now():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This link is invalid or has expired.")
    token.used_at = _now()
    return token


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
    """Block cross-site form posts that ride on the cookie (CSRF)."""
    origin = request.headers.get("origin")
    if origin is None:
        return True  # not sent by a browser cross-site request; SameSite=Lax covers the rest
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
    """Always answers the same way, so nobody can test which emails have accounts."""
    email = normalize_email(body.email)
    user = db.scalar(select(User).where(User.email == email).with_for_update())
    pending = hash_password(body.password)
    if user is None:
        user = User(email=email, display_name=body.display_name)
        db.add(user)
        db.flush()
        _send_confirm(db, user, pending)
    elif user.email_verified_at is None:
        if body.display_name:
            user.display_name = body.display_name
        _send_confirm(db, user, pending)
    else:
        _send_already_registered(db, user)
    db.commit()
    return CHECK_EMAIL


@router.post("/resend-confirmation", status_code=202, response_model=Status)
def resend_confirmation(body: EmailIn, db: DB) -> Status:
    user = db.scalar(select(User).where(User.email == normalize_email(body.email)))
    if user is not None and user.email_verified_at is None:
        latest = db.scalar(
            select(EmailToken)
            .where(EmailToken.user_id == user.id, EmailToken.purpose == "verify")
            .order_by(EmailToken.created_at.desc())
            .limit(1)
        )
        # Re-send with the same pending password; without one there is nothing to confirm.
        if latest is not None and latest.pending_password_hash:
            _send_confirm(db, user, latest.pending_password_hash)
            db.commit()
    return CHECK_EMAIL


@router.post("/confirm", response_model=Status)
def confirm_email(body: TokenIn, db: DB) -> Status:
    token = _use_email_token(db, body.token, "verify")
    user = db.get(User, token.user_id, with_for_update=True)
    if user.email_verified_at is None:
        user.email_verified_at = _now()
        if token.pending_password_hash:
            user.password_hash = token.pending_password_hash
        _expire_tokens(db, user, "verify")
    db.commit()
    return Status(status="confirmed")


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, response: Response, db: DB) -> LoginOut:
    settings = get_settings()
    now = _now()
    user = db.scalar(
        select(User).where(User.email == normalize_email(body.email)).with_for_update()
    )
    if user is not None and user.locked_until is not None and user.locked_until > now:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many wrong passwords. Try again later or reset your password.",
        )
    if not verify_password(user.password_hash if user else None, body.password):
        if user is not None:
            if user.email_verified_at is None and _matches_pending(db, user, body.password):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "Confirm your email first. Check your inbox for the link.",
                )
            user.failed_logins += 1
            if user.failed_logins >= settings.max_failed_logins:
                user.failed_logins = 0
                user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
            db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password.")

    user.failed_logins = 0
    user.locked_until = None
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


def _matches_pending(db: Session, user: User, password: str) -> bool:
    token = db.scalar(
        select(EmailToken)
        .where(
            EmailToken.user_id == user.id,
            EmailToken.purpose == "verify",
            EmailToken.used_at.is_(None),
            EmailToken.expires_at > _now(),
        )
        .order_by(EmailToken.created_at.desc())
        .limit(1)
    )
    return token is not None and verify_password(token.pending_password_hash, password)


@router.post("/refresh", response_model=LoginOut)
def refresh(body: RefreshIn, db: DB) -> LoginOut:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in again.")
    digest = hash_token(body.refresh_token)
    session = db.scalar(
        select(AuthSession)
        .where(AuthSession.token_hash == digest, AuthSession.client == "app")
        .with_for_update()
    )
    if session is None:
        # An old refresh token came back: it was probably stolen, so end that session.
        reused = db.scalar(select(AuthSession).where(AuthSession.previous_token_hash == digest))
        if reused is not None and reused.revoked_at is None:
            reused.revoked_at = _now()
            db.commit()
        raise unauthorized
    if not _live(session):
        raise unauthorized
    raw, new_digest = new_token()
    session.previous_token_hash = session.token_hash
    session.token_hash = new_digest
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
    user = db.scalar(select(User).where(User.email == normalize_email(body.email)))
    if user is not None:
        _send_reset(db, user)
        db.commit()
    return CHECK_EMAIL


@router.post("/reset-password", response_model=Status)
def reset_password(body: ResetIn, db: DB) -> Status:
    token = _use_email_token(db, body.token, "reset")
    user = db.get(User, token.user_id, with_for_update=True)
    user.password_hash = hash_password(body.password)
    user.email_verified_at = user.email_verified_at or _now()  # they proved they get the mail
    user.failed_logins = 0
    user.locked_until = None
    _expire_tokens(db, user, "reset")
    _expire_tokens(db, user, "verify")
    _revoke_all(db, user)  # sign out every device
    db.commit()
    return Status(status="password_changed")


me_router = APIRouter(tags=["auth"])


@me_router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut.of(user)
