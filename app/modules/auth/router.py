"""Auth endpoints: OTP signup, login/refresh/logout, OTP password reset, profile."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.email import EmailError, send_email
from app.core.email_templates import otp_email, welcome_email
from app.core.logging_setup import log_event
from app.core.rate_limit import check_key, rate_limit
from app.core.supabase_client import get_service_client
from app.modules.auth import backend as be
from app.modules.auth.backend import AuthBackend, get_backend
from app.modules.auth.dependencies import CurrentUser, get_current_user
from app.modules.auth.otp import OtpCooldown, OtpError, OtpService, SupabaseOtpStore
from app.modules.auth.schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    ProfileUpdate,
    RefreshRequest,
    ResendOtpRequest,
    ResetPasswordRequest,
    SignUpRequest,
    VerifyOtpRequest,
)

_log = logging.getLogger("auth")
router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(prefix="/me", tags=["profile"])

_OTP_MESSAGES = {
    "invalid_or_expired": "That code has expired or is not valid. Request a new one.",
    "invalid_code": "Incorrect code. Please try again.",
    "too_many_attempts": "Too many incorrect attempts. Request a new code.",
}


def get_otp_service() -> OtpService:
    return OtpService(SupabaseOtpStore(get_service_client()))


async def _send_otp(otp: OtpService, email: str, name: str, purpose: str) -> None:
    """Issue a code and email it. Cooldown -> 429; email failure -> 502."""
    try:
        code = await run_in_threadpool(otp.issue, email, purpose)
    except OtpCooldown as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {exc.retry_after}s before requesting another code.",
            headers={"Retry-After": str(exc.retry_after)},
        )
    subject, html = otp_email(name, code, purpose)
    try:
        await send_email(to=email, subject=subject, html=html, kind=f"otp-{purpose}")
    except EmailError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="We could not send the email. Please try again.")


def _raise_otp(exc: OtpError) -> None:
    raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=_OTP_MESSAGES.get(exc.kind, "Invalid code"))


# --- signup -----------------------------------------------------------------------------
@router.post("/signup", status_code=202, dependencies=[Depends(rate_limit("signup", 10, 3600))])
async def signup(
    body: SignUpRequest, backend: AuthBackend = Depends(get_backend), otp: OtpService = Depends(get_otp_service)
):
    check_key(f"signup:email:{body.email}", 5, 3600)
    meta = {"full_name": body.full_name, "phone": body.phone, "dob": body.dob.isoformat()}

    existing = await run_in_threadpool(backend.profile_by_email, body.email)
    if existing:
        if await run_in_threadpool(backend.is_confirmed, existing["user_id"]):
            raise HTTPException(status.HTTP_409_CONFLICT, detail="An account with this email already exists. Please sign in.")
        # Unverified earlier attempt: refresh the pending account and issue a new code.
        await run_in_threadpool(backend.update_pending_user, existing["user_id"], body.password, meta)
    else:
        try:
            await run_in_threadpool(backend.create_unconfirmed_user, body.email, body.password, meta)
        except be.DuplicateUser:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="An account with this email already exists. Please sign in.")

    await _send_otp(otp, body.email, body.full_name, "signup")
    return {"message": "Verification code sent", "email": body.email, "resend_after": settings.otp_resend_cooldown_seconds}


@router.post("/resend-otp", status_code=202, dependencies=[Depends(rate_limit("resend-otp", 10, 3600))])
async def resend_otp(
    body: ResendOtpRequest, backend: AuthBackend = Depends(get_backend), otp: OtpService = Depends(get_otp_service)
):
    check_key(f"resend:email:{body.email}", 5, 3600)
    profile = await run_in_threadpool(backend.profile_by_email, body.email)
    # Generic response whether or not a pending account exists (no enumeration).
    if profile and not await run_in_threadpool(backend.is_confirmed, profile["user_id"]):
        await _send_otp(otp, body.email, profile.get("full_name", ""), "signup")
    return {"message": "If a pending signup exists for this email, a new code has been sent"}


@router.post("/verify-otp", dependencies=[Depends(rate_limit("verify-otp", 20, 900))])
async def verify_otp(
    body: VerifyOtpRequest, backend: AuthBackend = Depends(get_backend), otp: OtpService = Depends(get_otp_service)
):
    profile = await run_in_threadpool(backend.profile_by_email, body.email)
    try:
        await run_in_threadpool(otp.verify, body.email, "signup", body.code)
    except OtpError as exc:
        _raise_otp(exc)
    if not profile:  # code verified but account vanished (e.g. cleaned up): treat as expired
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=_OTP_MESSAGES["invalid_or_expired"])

    await run_in_threadpool(backend.confirm_email, profile["user_id"])
    try:
        subject, html = welcome_email(profile.get("full_name", ""))
        await send_email(to=body.email, subject=subject, html=html, kind="welcome")
    except EmailError:
        log_event(_log, "welcome email failed (ignored)", {"email": body.email}, level=logging.WARNING)
    return {"verified": True}


# --- login / session --------------------------------------------------------------------
@router.post("/login", dependencies=[Depends(rate_limit("login", 30, 900))])
async def login(body: LoginRequest, backend: AuthBackend = Depends(get_backend)):
    check_key(f"login:email:{body.email}", 10, 900)
    try:
        session = await run_in_threadpool(backend.login, body.email, body.password)
    except be.EmailNotVerified:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="email_not_verified")
    except be.InvalidCredentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    profile = await run_in_threadpool(backend.profile_by_id, session.pop("user_id"))
    return {**session, "user": _public_profile(profile)}


@router.post("/refresh", dependencies=[Depends(rate_limit("refresh", 60, 900))])
async def refresh(body: RefreshRequest, backend: AuthBackend = Depends(get_backend)):
    try:
        return await run_in_threadpool(backend.refresh, body.refresh_token)
    except be.InvalidRefreshToken:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Session expired. Please sign in again.")


@router.post("/logout", status_code=204)
async def logout(user: CurrentUser = Depends(get_current_user), backend: AuthBackend = Depends(get_backend)):
    await run_in_threadpool(backend.sign_out, user.access_token)


# --- password reset ---------------------------------------------------------------------
@router.post("/forgot-password", status_code=202, dependencies=[Depends(rate_limit("forgot", 10, 3600))])
async def forgot_password(
    body: ForgotPasswordRequest, backend: AuthBackend = Depends(get_backend), otp: OtpService = Depends(get_otp_service)
):
    check_key(f"forgot:email:{body.email}", 5, 3600)
    profile = await run_in_threadpool(backend.profile_by_email, body.email)
    if profile and await run_in_threadpool(backend.is_confirmed, profile["user_id"]):
        try:
            await _send_otp(otp, body.email, profile.get("full_name", ""), "reset")
        except HTTPException as exc:
            # Cooldown / email failure must not reveal whether the account exists.
            log_event(_log, "forgot-password code not sent", {"status": exc.status_code}, level=logging.WARNING)
    return {"message": "If an account exists for this email, a reset code has been sent"}


@router.post("/reset-password", dependencies=[Depends(rate_limit("reset", 20, 900))])
async def reset_password(
    body: ResetPasswordRequest, backend: AuthBackend = Depends(get_backend), otp: OtpService = Depends(get_otp_service)
):
    try:
        await run_in_threadpool(otp.verify, body.email, "reset", body.code)
    except OtpError as exc:
        _raise_otp(exc)
    profile = await run_in_threadpool(backend.profile_by_email, body.email)
    if not profile:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=_OTP_MESSAGES["invalid_or_expired"])
    await run_in_threadpool(backend.set_password, profile["user_id"], body.new_password)
    return {"message": "Password updated. You can now sign in."}


# --- profile ----------------------------------------------------------------------------
def _public_profile(profile: dict | None) -> dict:
    profile = profile or {}
    return {
        "id": profile.get("user_id"),
        "email": profile.get("email"),
        "full_name": profile.get("full_name", ""),
        "phone": profile.get("phone", ""),
        "dob": profile.get("dob"),
    }


@me_router.get("")
async def get_me(user: CurrentUser = Depends(get_current_user), backend: AuthBackend = Depends(get_backend)):
    profile = await run_in_threadpool(backend.profile_by_id, user.id)
    if not profile:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return _public_profile(profile)


@me_router.patch("")
async def update_me(
    body: ProfileUpdate, user: CurrentUser = Depends(get_current_user), backend: AuthBackend = Depends(get_backend)
):
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Nothing to update")
    return _public_profile(await run_in_threadpool(backend.update_profile, user.id, fields))
