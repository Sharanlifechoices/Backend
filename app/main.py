"""FastAPI application factory."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging_setup import setup_logging
from app.core.request_logging import RequestLoggingMiddleware
from app.modules.auth import router as auth_router
from app.modules.cart import router as cart_router
from app.modules.catalog import router as catalog_router
from app.modules.jobs import router as jobs_router
from app.modules.orders import router as orders_router
from app.modules.unsubscribe import router as unsubscribe_router

_log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title="My LifeChoices API", version="0.1.0")

    # Order matters: the last-added middleware is outermost. Logging wraps CORS so
    # preflight requests and CORS rejections are logged too.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # bearer tokens, no cookies
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestLoggingMiddleware)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic's default body echoes the submitted value ("input"), which would return
        # (and log) passwords/codes. Keep only where and why the field failed.
        errors = [
            {"loc": list(e["loc"]), "msg": e["msg"].removeprefix("Value error, "), "type": e["type"]}
            for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    app.include_router(auth_router.router)
    app.include_router(auth_router.me_router)
    app.include_router(catalog_router.router)
    app.include_router(cart_router.router)
    app.include_router(orders_router.router)
    app.include_router(jobs_router.router)
    app.include_router(unsubscribe_router.router)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "env": settings.env, "email_mode": settings.email_mode}

    _log.info("app created (env=%s, email_mode=%s)", settings.env, settings.email_mode)
    if settings.env == "production":
        if settings.email_mode == "mock":
            _log.warning("EMAIL_MODE=mock in production: emails (including OTPs) are logged, not sent")
        for name in ("admin_secret", "jobs_secret"):
            if not getattr(settings, name):
                _log.warning("%s is empty in production: the related endpoints will reject all calls", name.upper())
        if settings.otp_secret == "change-me-in-prod":
            _log.error("OTP_SECRET is the default value: set a strong secret before going live")
    return app


app = create_app()
