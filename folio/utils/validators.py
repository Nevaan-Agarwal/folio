"""Validation helpers."""

from __future__ import annotations

import re


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_TAG_RE = re.compile(r"<[^>]*>")


def sanitize_input(value) -> str:
    if value is None:
        return ""
    cleaned = _TAG_RE.sub("", str(value))
    return cleaned.strip()


def validate_name(name: str) -> bool:
    cleaned = sanitize_input(name)
    return len(cleaned) >= 2


def validate_email(email: str) -> bool:
    cleaned = sanitize_input(email)
    return bool(_EMAIL_RE.match(cleaned))


def validate_password(password: str) -> dict:
    errors = []
    password = password or ""

    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if not any(char.isupper() for char in password):
        errors.append("Password must include at least one uppercase letter.")
    if not any(char.isdigit() for char in password):
        errors.append("Password must include at least one number.")

    return {"valid": not errors, "errors": errors}


def is_positive_amount(value) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False
