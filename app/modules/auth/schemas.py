from __future__ import annotations

import re
from datetime import date
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.passwords import validate_password

_PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()\-]{6,19}$")


def _clean_phone(value: str) -> str:
    value = value.strip()
    if not _PHONE_RE.match(value):
        raise ValueError("Enter a valid phone number (7-20 digits, may start with +)")
    return value


def _check_dob(value: date) -> date:
    if value > date.today():
        raise ValueError("Date of birth cannot be in the future")
    if value < date(1900, 1, 1):
        raise ValueError("Date of birth is not valid")
    return value


class _EmailModel(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class SignUpRequest(_EmailModel):
    full_name: str = Field(min_length=2, max_length=80)
    dob: date
    phone: str
    password: str = Field(max_length=128)

    _v_password = field_validator("password")(validate_password)
    _v_phone = field_validator("phone")(_clean_phone)
    _v_dob = field_validator("dob")(_check_dob)

    @field_validator("full_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Name is too short")
        return v


class VerifyOtpRequest(_EmailModel):
    code: str = Field(pattern=r"^\d{6}$")


class ResendOtpRequest(_EmailModel):
    pass


class LoginRequest(_EmailModel):
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=4096)


class ForgotPasswordRequest(_EmailModel):
    pass


class ResetPasswordRequest(_EmailModel):
    code: str = Field(pattern=r"^\d{6}$")
    new_password: str = Field(max_length=128)

    _v_password = field_validator("new_password")(validate_password)


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=80)
    phone: Optional[str] = None
    dob: Optional[date] = None

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: Optional[str]) -> Optional[str]:
        return _clean_phone(v) if v is not None else v

    @field_validator("dob")
    @classmethod
    def _dob(cls, v: Optional[date]) -> Optional[date]:
        return _check_dob(v) if v is not None else v
