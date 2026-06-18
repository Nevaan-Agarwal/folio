"""Rate limiter setup helper."""

from __future__ import annotations

import time

from flask import jsonify, request, session
from flask_limiter import Limiter

RATE_LIMITS = {
    "/login": "5 per 10 minutes",
    "/register": "3 per hour",
    "/receipts/upload": "20 per hour",
    "/api/agent": "30 per hour",
    "/forgot-password": "3 per 15 minutes",
    "/forms/*/approve": "50 per hour",
    "/admin/users/*/promote": "10 per hour",
    "default": "200 per hour",
}

AUTH_PATHS = {"/auth/login", "/auth/register", "/auth/forgot-password"}


def _extract_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def auth_rate_limit_key() -> str:
    return _extract_ip()


def user_rate_limit_key() -> str:
    return session.get("uid") or _extract_ip()


def default_rate_limit_key() -> str:
    if request.path in AUTH_PATHS:
        return auth_rate_limit_key()
    return user_rate_limit_key()


def _rate_limit_breach_handler(request_limit):
    retry_after = 0
    reset_at = getattr(request_limit, "reset_at", None)
    if reset_at is not None:
        try:
            timestamp = reset_at.timestamp() if hasattr(reset_at, "timestamp") else float(reset_at)
            retry_after = max(0, int(timestamp - time.time()))
        except (TypeError, ValueError):
            retry_after = 0
    return jsonify({"error": "Rate limit exceeded", "retryAfter": retry_after, "code": 429}), 429


limiter = Limiter(
    key_func=default_rate_limit_key,
    default_limits=[RATE_LIMITS["default"]],
    storage_uri="memory://",
    on_breach=_rate_limit_breach_handler,
)


def create_limiter(app):
    storage_uri = app.config.get("RATELIMIT_STORAGE_URI", "memory://")
    if app.config.get("ENV") == "production" and storage_uri == "memory://":
        storage_uri = "redis://localhost:6379/0"
    app.config["RATELIMIT_STORAGE_URI"] = storage_uri
    limiter.init_app(app)
    return limiter
