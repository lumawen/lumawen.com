"""Sign in with Google.

The web dashboard and the Android app use Google's own sign-in to get an ID token, and send
it here. We check it with Google's public keys and accept it only if it was issued to one of
our OAuth client IDs (LUMAWEN_GOOGLE_CLIENT_IDS) for a verified email address.

Google's "email_verified" only proves the address *now* for Gmail addresses and Google
Workspace accounts (tokens with an "hd" claim). Other addresses were verified once, maybe years
ago, by someone who may no longer own them. So:

- A Google account already linked: sign in as that user.
- Gmail or Workspace address: link (creating the account if needed) and sign in. An unconfirmed
  account becomes confirmed and its pending sign-ups (passwords chosen by whoever signed up)
  are cancelled.
- Any other address, whether or not it has an account: answer 202 "check your email" and email
  a link. Opening it and continuing with the SAME Google account links it and signs in, so it
  takes both the inbox now and that Google account. The answer is the same either way, so this
  can't be used to test who has an account. If the inbox's real owner later resets the
  password, the reset also removes linked Google accounts.
"""

import json
import threading
import time
import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .auth import (
    BAD_LINK,
    CHECK_EMAIL,
    DB,
    SIGNUPS_PER_IP_PER_HOUR,
    VERIFY_HOURS,
    LoginOut,
    Status,
    _client_ip,
    _count_attempt,
    _expire_tokens,
    _issue_email_token,
    _now,
    _same_origin,
    normalize_email,
    sign_in,
)
from .config import get_settings
from .mail import queue_email, recent_count
from .models import EmailToken, User, UserIdentity
from .security import hash_token

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
GOOGLE_OWNED_DOMAINS = {"gmail.com", "googlemail.com"}
FETCH_TIMEOUT = 5  # seconds; google-auth's default is 120


class CachingRequest:
    """google-auth transport that caches Google's signing certs for as long as Google says.

    Without it, every sign-in fetches the certs again (and can wait 120 s if Google is slow).
    """

    def __init__(self, inner=None):
        self.inner = inner or google_requests.Request()
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, object]] = {}

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        if method != "GET":
            return self.inner(url, method, body, headers, timeout=FETCH_TIMEOUT, **kwargs)
        with self._lock:
            hit = self._cache.get(url)
            if hit and hit[0] > time.monotonic():
                return hit[1]
        response = self.inner(url, method, body, headers, timeout=FETCH_TIMEOUT, **kwargs)
        if response.status == 200 and _is_json_object(response.data):
            with self._lock:
                self._cache[url] = (time.monotonic() + _max_age(response.headers), response)
        return response


def _is_json_object(data) -> bool:
    """Only cache real key sets, not e.g. a proxy's HTML page served with status 200."""
    try:
        return isinstance(json.loads(data), dict)
    except (TypeError, ValueError):
        return False


def _max_age(headers) -> int:
    for part in (headers or {}).get("cache-control", "").split(","):
        name, _, value = part.strip().partition("=")
        if name.lower() == "max-age" and value.isdigit():
            return min(int(value), 6 * 3600)
    return 3600


_transport = CachingRequest()


class GoogleIn(BaseModel):
    id_token: str = Field(max_length=4096)
    client: Literal["web", "app"] = "web"
    device_name: str | None = Field(None, max_length=100)


class GoogleConfirmIn(GoogleIn):
    token: str = Field(min_length=10, max_length=100)


def fetch_claims(token: str, audiences: list[str]) -> dict:
    """Check signature, expiry, issuer and audience with Google's keys. Some tests replace this."""
    return id_token.verify_oauth2_token(
        token, _transport, audience=audiences, clock_skew_in_seconds=10
    )


def _verified_claims(token: str) -> dict:
    audiences = get_settings().google_audiences
    if not audiences:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google sign-in isn't set up.")
    bad = HTTPException(status.HTTP_401_UNAUTHORIZED, "Google sign-in failed. Try again.")
    try:
        claims = fetch_claims(token, sorted(audiences))
    except google_exceptions.TransportError as exc:  # couldn't reach Google for its keys
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Google sign-in is unavailable. Try again soon."
        ) from exc
    except (ValueError, google_exceptions.GoogleAuthError) as exc:  # forged, expired, wrong app
        raise bad from exc
    if claims.get("aud") not in audiences or claims.get("iss") not in GOOGLE_ISSUERS:
        raise bad
    if not isinstance(claims.get("sub"), str) or not isinstance(claims.get("email"), str):
        raise bad
    if not claims["sub"] or not claims["email"]:
        raise bad
    if claims.get("email_verified") is not True:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Your Google account's email isn't verified by Google."
        )
    email = normalize_email(str(claims["email"]))
    if not email.isascii() or len(email) > 320:
        raise bad
    domain = email.rpartition("@")[2]
    hosted = claims.get("hd")
    google_owned = domain in GOOGLE_OWNED_DOMAINS or (
        isinstance(hosted, str) and hosted.lower() == domain
    )
    return {
        "sub": str(claims["sub"]),
        "email": email,
        "name": claims.get("name"),
        "google_owned": google_owned,
    }


def _linked_user(db, sub: str) -> User | None:
    identity = db.scalar(
        select(UserIdentity).where(UserIdentity.provider == "google", UserIdentity.subject == sub)
    )
    return None if identity is None else db.get(User, identity.user_id)


def _send_google_confirm(db, user: User, sub: str) -> None:
    if recent_count(db, user.email, "google_confirm") >= get_settings().emails_per_hour:
        return
    raw = _issue_email_token(db, user, "google", timedelta(hours=VERIFY_HOURS), google_subject=sub)
    link = f"{get_settings().dashboard_url}/#/google-confirm?token={raw}"
    queue_email(
        db,
        user.email,
        "Confirm Google sign-in for Lumawen",
        "Hi,\n\n"
        "Someone (hopefully you) tried to sign in to Lumawen with Google using this email. "
        "To finish, open this link and continue with the same Google account:\n\n"
        f"{link}\n\n"
        f"The link works for {VERIFY_HOURS} hours. If this wasn't you, ignore this email and "
        "nothing changes.\n\n"
        "Lumawen\nhttps://lumawen.com\n",
        "google_confirm",
    )


def _link_and_sign_in(db, user: User, claims: dict, body: GoogleIn, response: Response):
    """The caller has locked `user` and proven the address is theirs now."""
    if user.email_verified_at is None:
        user.email_verified_at = _now()
        _expire_tokens(db, user, "verify")  # cancel sign-ups nobody confirmed
    if user.display_name is None and claims["name"]:
        user.display_name = str(claims["name"])[:80]
    db.execute(
        pg_insert(UserIdentity)
        .values(
            id=uuid.uuid4(),
            user_id=user.id,
            provider="google",
            subject=claims["sub"],
            email=claims["email"],
        )
        .on_conflict_do_nothing(constraint="user_identities_provider_subject")
    )
    linked = db.scalar(
        select(UserIdentity.user_id).where(
            UserIdentity.provider == "google", UserIdentity.subject == claims["sub"]
        )
    )
    if linked != user.id:  # a parallel request linked this Google account to someone else
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Try signing in again.")
    return sign_in(db, user, body.client, body.device_name, response)


@router.post(
    "/google",
    response_model=LoginOut,
    responses={202: {"model": Status, "description": "Check your email to finish."}},
)
def google_sign_in(body: GoogleIn, request: Request, response: Response, db: DB):
    if body.client == "web" and not _same_origin(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-site request blocked.")
    claims = _verified_claims(body.id_token)

    linked_user = _linked_user(db, claims["sub"])
    if linked_user is not None:
        return sign_in(db, linked_user, body.client, body.device_name, response)

    if not claims["google_owned"]:
        # Same limit as register: each of these can email a stranger.
        rule = (f"register:{_client_ip(request)}", SIGNUPS_PER_IP_PER_HOUR, timedelta(hours=1))
        if not _count_attempt(db, [rule]):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "Too many sign-ups from here. Try again later."
            )

    # Same pattern as register: create the row if missing, then lock it (users first).
    db.execute(
        pg_insert(User)
        .values(id=uuid.uuid4(), email=claims["email"], role="user")
        .on_conflict_do_nothing(index_elements=["email"])
    )
    user = db.scalar(select(User).where(User.email == claims["email"]).with_for_update())
    if not claims["google_owned"]:
        _send_google_confirm(db, user, claims["sub"])
        db.commit()
        return JSONResponse(CHECK_EMAIL.model_dump(), status_code=status.HTTP_202_ACCEPTED)
    return _link_and_sign_in(db, user, claims, body, response)


@router.post("/google/confirm", response_model=LoginOut)
def google_confirm(body: GoogleConfirmIn, request: Request, response: Response, db: DB):
    """Finish a Google sign-in from the emailed link: the link proves the inbox, and the ID
    token must be from the same Google account that started it."""
    if body.client == "web" and not _same_origin(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-site request blocked.")
    claims = _verified_claims(body.id_token)
    bad = HTTPException(status.HTTP_400_BAD_REQUEST, BAD_LINK)
    user_id = db.scalar(
        select(EmailToken.user_id).where(
            EmailToken.token_hash == hash_token(body.token), EmailToken.purpose == "google"
        )
    )
    if user_id is None:
        raise bad
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    token = db.scalar(
        select(EmailToken).where(EmailToken.token_hash == hash_token(body.token)).with_for_update()
    )
    if (
        token is None
        or token.used_at is not None
        or token.expires_at <= _now()
        or token.google_subject != claims["sub"]
        or user.email != claims["email"]
    ):
        db.rollback()
        raise bad
    token.used_at = _now()
    return _link_and_sign_in(db, user, claims, body, response)
