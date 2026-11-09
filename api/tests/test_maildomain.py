"""Sign-ups to domains that can't receive mail are refused; DNS trouble never blocks anyone."""

from types import SimpleNamespace

import dns.exception
import dns.resolver
import pytest
from helpers import PASSWORD

from lumawen_api import maildomain
from lumawen_api.config import get_settings
from lumawen_api.maildomain import CANT_RECEIVE, accepts_mail


class FakeResolver:
    """Answers from a dict: {(domain, rdtype): [records] or an exception class}."""

    def __init__(self, answers):
        self.answers = answers

    def resolve(self, domain, rdtype):
        answer = self.answers.get((domain, rdtype), dns.resolver.NoAnswer)
        if isinstance(answer, type) and issubclass(answer, BaseException):
            raise answer()
        return answer


MX = [SimpleNamespace(preference=10, exchange="mx.mail.test.")]
NULL_MX = [SimpleNamespace(preference=0, exchange=".")]
GLOBAL_A = [SimpleNamespace(address="93.184.215.14")]


@pytest.fixture
def dns_check(monkeypatch):
    monkeypatch.setenv("LUMAWEN_CHECK_MAIL_DOMAINS", "true")
    get_settings.cache_clear()

    def use(answers):
        known_good = {(maildomain.KNOWN_GOOD, "MX"): MX}
        monkeypatch.setattr(maildomain, "_resolver", FakeResolver(known_good | answers))

    yield use
    monkeypatch.undo()
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("answers", "ok"),
    [
        ({("good.test", "MX"): MX}, True),
        ({("good.test", "A"): GLOBAL_A}, True),  # no MX, but an address: mail goes there
        ({("good.test", "MX"): NULL_MX}, False),  # "accepts no mail", like example.com
        ({("good.test", "MX"): dns.resolver.NXDOMAIN}, False),  # no such domain
        ({}, False),  # no MX and no address
        ({("good.test", "TXT"): [SimpleNamespace(strings=[b"v=spf1 -all"])]}, False),
        ({("good.test", "MX"): dns.exception.Timeout}, True),  # DNS trouble: let it through
        ({("good.test", "MX"): dns.resolver.NoNameservers}, True),
        ({("good.test", "MX"): RuntimeError}, True),
    ],
)
def test_accepts_mail(dns_check, answers, ok):
    dns_check(answers)
    assert accepts_mail("someone@good.test") is ok


def test_sign_up_and_mailing_list_refuse_dead_domains(client, dns_check):
    dns_check({("example.com", "MX"): NULL_MX, ("real-mail.net", "MX"): MX})
    for path, extra in (
        ("/api/auth/register", {"password": PASSWORD}),
        ("/api/list/subscribe", {}),
    ):
        r = client.post(path, json={"email": "victim-x@example.com", **extra})
        assert r.status_code == 422 and r.json()["detail"] == CANT_RECEIVE
        assert client.post(path, json={"email": "ok@real-mail.net", **extra}).status_code == 202


def test_broken_server_dns_blocks_nobody(dns_check):
    dns_check({(maildomain.KNOWN_GOOD, "MX"): dns.resolver.NXDOMAIN})  # says nothing exists
    assert accepts_mail("someone@example.com")


def test_check_is_off_unless_enabled():
    assert accepts_mail("anyone@example.com")  # tests run with the check turned off
