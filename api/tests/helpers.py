"""Shared test helpers: read links out of queued emails, register and sign in."""

import re

from sqlalchemy import select

from lumawen_api.db import _session_factory
from lumawen_api.models import OutboxEmail

PASSWORD = "correct horse battery"


def outbox(to: str | None = None, kind: str | None = None) -> list[OutboxEmail]:
    with _session_factory()() as db:
        query = select(OutboxEmail).order_by(OutboxEmail.id)
        if to:
            query = query.where(OutboxEmail.to_address == to)
        if kind:
            query = query.where(OutboxEmail.kind == kind)
        return list(db.scalars(query))


def link_token(email: OutboxEmail) -> str:
    match = re.search(r"token=([A-Za-z0-9_-]+)", email.body_text)
    assert match, email.body_text
    return match.group(1)


def register(client, email: str, password: str = PASSWORD) -> str:
    """Register and return the confirm token from the email."""
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 202, r.text
    return link_token(outbox(email.lower(), "confirm")[-1])


def signed_up(client, email: str, password: str = PASSWORD) -> None:
    token = register(client, email, password)
    assert client.post("/api/auth/confirm", json={"token": token}).status_code == 200


def web_login(client, email: str, password: str = PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def app_login(client, email: str, password: str = PASSWORD) -> dict:
    r = client.post(
        "/api/auth/login",
        json={"email": email, "password": password, "client": "app", "device_name": "Pixel"},
    )
    assert r.status_code == 200, r.text
    return r.json()
