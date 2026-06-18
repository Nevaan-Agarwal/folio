"""Session-based authentication and authorization middleware."""

from functools import wraps
import re

from flask import abort, flash, g, redirect, session, url_for


def require_auth(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        uid = session.get("uid")
        if not uid:
            flash("Please sign in to continue.", "error")
            return redirect(url_for("auth.login_page"))

        g.user = {
            "uid": uid,
            "email": session.get("email"),
            "role": session.get("role", "employee"),
            "lang": session.get("lang", "en"),
            "name": session.get("name", ""),
        }
        return view_func(*args, **kwargs)

    return wrapped


def require_admin(view_func):
    @require_auth
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if session.get("role") != "admin":
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped


_PROMPT_INJECTION_PATTERNS = (
    r"ignore\s+previous\s+instructions?",
    r"you\s+are\s+now",
    r"\bsystem\s*:",
    r"###",
)


def sanitize_ocr_text(text: str) -> str:
    """
    Sanitize OCR text before it is sent to AI extraction endpoints.

    - Removes common prompt-injection phrases.
    - Removes null bytes and control characters.
    - Truncates output to 4000 characters.
    """
    cleaned = str(text or "")
    cleaned = cleaned.replace("\x00", "")
    cleaned = re.sub(r"[\x01-\x08\x0B-\x1F\x7F]", "", cleaned)

    for pattern in _PROMPT_INJECTION_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned[:4000]
