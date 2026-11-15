"""Response headers for the website and the private model service."""

from __future__ import annotations

from collections.abc import MutableMapping

HSTS_VALUE = "max-age=31536000; includeSubDomains"
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
WEBSITE_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


def apply_security_headers(headers: MutableMapping[str, str], *, policy: str, secure: bool) -> None:
    headers["Content-Security-Policy"] = policy
    headers["X-Content-Type-Options"] = "nosniff"
    if secure:
        headers["Strict-Transport-Security"] = HSTS_VALUE
