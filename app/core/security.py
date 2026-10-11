"""JWT verification against Supabase's JWKS endpoint (ES256 / RS256).

PyJWKClient caches the key set (600 s) and refreshes on unknown kid (key rotation).
If SUPABASE_JWT_SECRET is set, legacy HS256 tokens are also accepted.
"""

from __future__ import annotations

from typing import Any

import jwt
from jwt import ExpiredSignatureError, InvalidTokenError, PyJWKClient

from app.core.config import settings

_jwks_client: PyJWKClient | None = None

_ALGORITHMS_ASYMMETRIC = ["ES256", "RS256"]
_ALGORITHMS_LEGACY = ["HS256"]
_CLOCK_LEEWAY_SECONDS = 30


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(settings.jwks_url, lifespan=600, cache_jwk_set=True)
    return _jwks_client


def decode_token(token: str) -> dict[str, Any]:
    """Verify a Supabase JWT and return its claims. Raises InvalidTokenError on failure."""
    # leeway: a token minted by Supabase can carry an iat a fraction of a second ahead of this server's clock,
    # which would otherwise reject a brand-new token (login followed by an immediate request).
    common = dict(audience="authenticated", issuer=settings.jwt_issuer, options={"require": ["exp", "sub", "aud"]}, leeway=_CLOCK_LEEWAY_SECONDS)
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        return jwt.decode(token, signing_key.key, algorithms=_ALGORITHMS_ASYMMETRIC, **common)
    except ExpiredSignatureError:
        raise
    except Exception as exc:
        # JWKS unreachable / unknown kid / bad signature: try the legacy secret if configured.
        if not settings.supabase_jwt_secret:
            if isinstance(exc, InvalidTokenError):
                raise
            raise InvalidTokenError(str(exc)) from exc
        return jwt.decode(token, settings.supabase_jwt_secret, algorithms=_ALGORITHMS_LEGACY, **common)


__all__ = ["decode_token", "ExpiredSignatureError", "InvalidTokenError"]
