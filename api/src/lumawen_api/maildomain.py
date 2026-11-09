"""Refuse sign-ups to addresses whose domain can't receive email (example.com, gmial.co).

Only a clear answer from DNS rejects an address: the domain doesn't exist, has no mail server,
or says it takes no mail. A timeout or any other DNS trouble lets the address through, so an
outage never blocks real people. So does a "no" when a domain that surely takes mail gets the
same answer: then the server's own DNS is broken, not the address.
"""

import logging

import dns.resolver
from email_validator import EmailUndeliverableError
from email_validator.deliverability import caching_resolver, validate_email_deliverability

from .config import get_settings

CANT_RECEIVE = "That email address can't receive mail. Check it for typos."

KNOWN_GOOD = "gmail.com"

log = logging.getLogger("lumawen.maildomain")
_resolver = caching_resolver(timeout=3)  # answers are cached for their DNS lifetime


def _dns_works() -> bool:
    try:
        _resolver.resolve(KNOWN_GOOD, "MX")
    except Exception:
        log.warning("DNS can't find %s; not checking email domains", KNOWN_GOOD)
        return False
    return True


def accepts_mail(email: str) -> bool:
    if not get_settings().check_mail_domains:
        return True
    domain = email.rsplit("@", 1)[-1]
    try:
        validate_email_deliverability(domain, domain, dns_resolver=_resolver)
    except EmailUndeliverableError as e:
        # No cause: a null MX or "v=spf1 -all". Any other cause is DNS trouble: let it through.
        clear_no = e.__cause__ is None or isinstance(
            e.__cause__, dns.resolver.NXDOMAIN | dns.resolver.NoAnswer
        )
        return not (clear_no and _dns_works())
    return True
