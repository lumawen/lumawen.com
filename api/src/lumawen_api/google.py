"""Sign in with Google.

The web dashboard and the Android app use Google's own sign-in to get an ID token, and send
it here. We check it with Google's public keys and accept it only if it was issued to one of
our OAuth client IDs (LUMAWEN_GOOGLE_CLIENT_IDS) for a verified email address.

Google's "email_verified" only proves the address *now* for Gmail addresses and Google
Workspace accounts (tokens with an "hd" claim). Other addresses were verified once, maybe years
ago, by someone who may no longer own them. So:

- A Google account already linked: sign in as that user.
- Gmail or Workspace address matching a user: link and sign in. An unconfirmed account
  becomes confirmed and its pending sign-ups (passwords chosen by whoever signed up) are
  cancelled.
- Any other address matching a user: refused. They sign in with their password instead.
- No user with that email: create a confirmed account with no password. If the inbox's real
  owner later resets the password, the reset also removes linked Google accounts.
"""

import json
import threading
import time
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .auth import DB, LoginOut, _expire_tokens, _now, _same_origin, normalize_email, sign_in
from .config import get_settings
from .models import User, UserIdentity

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


@router.post("/google", response_model=LoginOut)
def google_sign_in(body: GoogleIn, request: Request, response: Response, db: DB) -> LoginOut:
    if body.client == "web" and not _same_origin(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-site request blocked.")
    claims = _verified_claims(body.id_token)

    linked_user = _linked_user(db, claims["sub"])
    if linked_user is not None:
        return sign_in(db, linked_user, body.client, body.device_name, response)

    # Same pattern as register: create the row if missing, then lock it (users first).
    created = db.scalar(
        pg_insert(User)
        .values(id=uuid.uuid4(), email=claims["email"], role="user")
        .on_conflict_do_nothing(index_elements=["email"])
        .returning(User.id)
    )
    if created is None and not claims["google_owned"]:
        db.rollback()
        # A parallel first sign-in (double tap, app retry) may have just created and linked it.
        linked_user = _linked_user(db, claims["sub"])
        if linked_user is not None:
            return sign_in(db, linked_user, body.client, body.device_name, response)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "There's already a Lumawen account with this email. Sign in with your password.",
        )
    user = db.scalar(select(User).where(User.email == claims["email"]).with_for_update())
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
