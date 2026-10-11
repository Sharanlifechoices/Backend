"""Authentication dependency: Bearer access token verified locally against Supabase JWKS."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException, status
from jwt import ExpiredSignatureError, InvalidTokenError

from app.core.logging_setup import user_id_var
from app.core.security import decode_token


@dataclass
class CurrentUser:
    id: str
    email: Optional[str]
    access_token: str


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"})


async def get_current_user(authorization: Optional[str] = Header(default=None)) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized("Not authenticated")
    token = authorization[7:].strip()
    try:
        claims = decode_token(token)
    except ExpiredSignatureError:
        raise _unauthorized("token_expired")  # client should call /auth/refresh and retry
    except InvalidTokenError:
        raise _unauthorized("Invalid token")
    user_id_var.set(claims["sub"])  # makes every later log line carry the user id
    return CurrentUser(id=claims["sub"], email=claims.get("email"), access_token=token)
