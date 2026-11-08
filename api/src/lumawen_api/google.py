"""Sign in with Google.

The web dashboard and the Android app use Google's own sign-in to get an ID token, and send
it here. We check it with Google's public keys and accept it only if it was issued to one of
our OAuth client IDs (LUMAWEN_GOOGLE_CLIENT_IDS) for a verified email address.

Linking rules:
- A Google account already linked: sign in as that user.
- Otherwise the Google email matches a user: Google has proved the address, so link and sign
  in. If that account was never confirmed, it becomes confirmed and any pending sign-ups
  (with passwords chosen by whoever signed up) are cancelled.
- Otherwise: create a confirmed account with no password (they can set one later with
  "forgot password").
"""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
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
_transport = google_requests.Request()


class GoogleIn(BaseModel):
    id_token: str = Field(max_length=4096)
    client: Literal["web", "app"] = "web"
    device_name: str | None = Field(None, max_length=100)


def fetch_claims(token: str) -> dict:
    """Check the token's signature and expiry with Google's keys. Tests replace this."""
    return id_token.verify_oauth2_token(token, _transport, audience=None)


def _verified_claims(token: str) -> dict:
    audiences = get_settings().google_audiences
    if not audiences:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google sign-in isn't set up.")
    bad = HTTPException(status.HTTP_401_UNAUTHORIZED, "Google sign-in failed. Try again.")
    try:
        claims = fetch_claims(token)
    except ValueError as exc:  # bad signature, expired, malformed
        raise bad from exc
    if claims.get("aud") not in audiences or claims.get("iss") not in GOOGLE_ISSUERS:
        raise bad
    if not claims.get("sub") or not claims.get("email"):
        raise bad
    if claims.get("email_verified") is not True:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Your Google account's email isn't verified by Google."
        )
    email = normalize_email(str(claims["email"]))
    if not email.isascii() or len(email) > 320:
        raise bad
    return {"sub": str(claims["sub"]), "email": email, "name": claims.get("name")}


@router.post("/google", response_model=LoginOut)
def google_sign_in(body: GoogleIn, request: Request, response: Response, db: DB) -> LoginOut:
    if body.client == "web" and not _same_origin(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-site request blocked.")
    claims = _verified_claims(body.id_token)

    identity = db.scalar(
        select(UserIdentity).where(
            UserIdentity.provider == "google", UserIdentity.subject == claims["sub"]
        )
    )
    if identity is not None:
        user = db.get(User, identity.user_id)
        return sign_in(db, user, body.client, body.device_name, response)

    # Same pattern as register: create the row if missing, then lock it (users first).
    db.execute(
        pg_insert(User)
        .values(id=uuid.uuid4(), email=claims["email"], role="user")
        .on_conflict_do_nothing(index_elements=["email"])
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
