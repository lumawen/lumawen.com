"""Google sign-in through the real google-auth verifier, with a local signing key."""

import datetime as dt
import json

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from google.auth import crypt, jwt
from google.auth.exceptions import TransportError

from lumawen_api import config, google

WEB_ID = "web-client.apps.googleusercontent.com"


class FakeResponse:
    def __init__(self, status: int, data: bytes, headers: dict | None = None):
        self.status, self.data, self.headers = status, data, headers or {}


class FakeGoogle:
    """Serves certs like https://www.googleapis.com/oauth2/v1/certs and counts fetches."""

    def __init__(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
        now = dt.datetime.now(dt.UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(1)
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=1))
            .sign(key, hashes.SHA256())
        )
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        self.signer = crypt.RSASigner.from_string(pem, key_id="kid1")
        self.certs = {"kid1": cert.public_bytes(serialization.Encoding.PEM).decode()}
        self.fetches = 0
        self.down = False
        self.html = False

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        assert timeout == 5
        self.fetches += 1
        if self.down:
            raise TransportError("connection timed out")
        if self.html:
            return FakeResponse(200, b"<html>Sign in to the wifi</html>")
        return FakeResponse(200, json.dumps(self.certs).encode(), {"cache-control": "max-age=600"})

    def token(self, **overrides) -> str:
        now = int(dt.datetime.now(dt.UTC).timestamp())
        payload = {
            "iss": "https://accounts.google.com",
            "aud": WEB_ID,
            "sub": "g-real",
            "email": "real@gmail.com",
            "email_verified": True,
            "iat": now,
            "exp": now + 3600,
            **overrides,
        }
        return jwt.encode(self.signer, payload).decode()


@pytest.fixture
def fake_google(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(google, "_transport", google.CachingRequest(inner=fake))
    monkeypatch.setattr(config.get_settings(), "google_client_ids", WEB_ID)
    return fake


def login(client, token):
    return client.post("/api/auth/google", json={"id_token": token, "client": "app"})


def test_real_token_signs_in_and_certs_are_cached(client, fake_google):
    assert login(client, fake_google.token()).status_code == 200
    assert login(client, fake_google.token()).status_code == 200
    assert fake_google.fetches == 1


def test_token_for_another_app_is_refused(client, fake_google):
    assert (
        login(client, fake_google.token(aud="other.apps.googleusercontent.com")).status_code == 401
    )


def test_wrong_issuer_is_refused(client, fake_google):
    assert login(client, fake_google.token(iss="https://evil.example")).status_code == 401


def test_expired_token_is_refused(client, fake_google):
    old = int(dt.datetime.now(dt.UTC).timestamp()) - 7200
    assert login(client, fake_google.token(iat=old, exp=old + 3600)).status_code == 401


def test_tampered_token_is_refused(client, fake_google):
    head, body, sig = fake_google.token().split(".")
    assert login(client, f"{head}.{body}.{sig[:-4]}AAAA").status_code == 401
    assert login(client, "garbage").status_code == 401


def test_google_unreachable_gives_503_not_500(client, fake_google):
    fake_google.down = True
    assert login(client, fake_google.token()).status_code == 503


def test_bad_200_from_a_proxy_is_not_cached(client, fake_google):
    fake_google.html = True
    assert login(client, fake_google.token()).status_code == 401
    fake_google.html = False
    assert login(client, fake_google.token()).status_code == 200
