"""Same-site checks and browser headers for the Flask website."""

from __future__ import annotations

import os

from flask import Flask, jsonify, request

from backend.http_security import WEBSITE_CSP, apply_security_headers

_UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _allowed_origins() -> set[str]:
    origins = {request.host_url.rstrip("/")}
    configured = os.environ.get("BETTER_AUTH_URL", "").strip().rstrip("/")
    if configured:
        origins.add(configured)
    return origins


def install(app: Flask) -> None:
    @app.before_request
    def reject_cross_site_mutation():
        # A missing Origin stays allowed so curl, tests, and the agent can call the API.
        if request.method not in _UNSAFE:
            return None
        origin = request.headers.get("Origin")
        cross_site = request.headers.get("Sec-Fetch-Site") == "cross-site"
        if cross_site or (origin is not None and origin not in _allowed_origins()):
            return jsonify(error="cross-site request rejected"), 403
        return None

    @app.after_request
    def add_security_headers(response):
        apply_security_headers(response.headers, policy=WEBSITE_CSP, secure=request.is_secure)
        return response
