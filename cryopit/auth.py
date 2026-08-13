"""Request identity supplied by an institutional SSO boundary.

CryoPit never handles credentials. In an SSO deployment a trusted reverse
proxy authenticates the user, strips any client-supplied identity header, and
injects one verified immutable identifier. The header is honored only when the
deployer explicitly enables trusted-proxy authentication.

With proxy authentication disabled, every browser maps to ``DEV_USER``. That
is intentional for a single field laptop and unsafe for a shared hosted URL.
"""
from __future__ import annotations

import unicodedata

from flask import has_request_context, request

from .config import AUTH_HEADER, DEV_USER, IDENTITY_MAX_LENGTH, TRUST_PROXY_AUTH


def normalize_identity(value) -> str:
    """Return one canonical owner identifier or raise ``ValueError``.

    We deliberately do not limit identifiers to email-like syntax: many SSO
    systems expose opaque OIDC subjects. Control/format characters are rejected
    because they make logs and audit records ambiguous. Unicode is normalized
    to NFC so canonically equivalent identities do not become distinct owners.
    """
    if not isinstance(value, str):
        raise ValueError("missing identity")
    identity = unicodedata.normalize("NFC", value.strip())
    if not identity:
        raise ValueError("missing identity")
    if len(identity) > IDENTITY_MAX_LENGTH:
        raise ValueError("identity is too long")
    if any(unicodedata.category(ch) in {"Cc", "Cf", "Cs"} for ch in identity):
        raise ValueError("identity contains control characters")
    return identity


def identity_from_headers(headers) -> str:
    """Resolve the configured trusted header from a mapping.

    Kept separate from Flask's request object so the SSO boundary can be unit
    tested without a live web server.
    """
    return normalize_identity(headers.get(AUTH_HEADER))


def current_user() -> str:
    if not TRUST_PROXY_AUTH:
        return normalize_identity(DEV_USER)
    if not has_request_context():
        raise RuntimeError("Trusted proxy identity is only available in a request context.")
    try:
        return identity_from_headers(request.headers)
    except ValueError:
        from flask import abort
        # Fail closed. Never fall back to the shared development owner when a
        # production deployment claims the proxy is authoritative.
        abort(401, description="Authenticated institutional identity required.")
