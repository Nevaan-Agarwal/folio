from __future__ import annotations

import time
from datetime import timedelta
from uuid import uuid4

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from middleware.auth_middleware import require_auth
from middleware.rate_limiter import RATE_LIMITS, auth_rate_limit_key, limiter
from repositories import audit_repository, user_repository
from utils.helpers import get_locale_payload
from utils.validators import (
    sanitize_input,
    validate_email,
    validate_name,
    validate_password,
)

auth_bp = Blueprint("auth", __name__)
FAILED_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
FAILED_ATTEMPT_WINDOW_SECONDS = 600
FAILED_ATTEMPT_LIMIT = 5
ALLOWED_ROLES = {"employee", "admin"}


def _wants_json_response() -> bool:
    if request.is_json:
        return True
    accepted = request.headers.get("Accept", "")
    return "application/json" in accepted


def _client_key() -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _too_many_failed_attempts(key: str) -> bool:
    now = time.time()
    recent_attempts = [
        ts
        for ts in FAILED_LOGIN_ATTEMPTS.get(key, [])
        if now - ts <= FAILED_ATTEMPT_WINDOW_SECONDS
    ]
    FAILED_LOGIN_ATTEMPTS[key] = recent_attempts
    return len(recent_attempts) >= FAILED_ATTEMPT_LIMIT


def _record_failed_attempt(key: str) -> None:
    FAILED_LOGIN_ATTEMPTS.setdefault(key, []).append(time.time())


def _clear_failed_attempts(key: str) -> None:
    FAILED_LOGIN_ATTEMPTS.pop(key, None)


def _create_session(user, remember_me: bool) -> None:
    session["uid"] = user.id
    session["email"] = user.email
    session["role"] = user.role
    session["lang"] = user.language
    session["name"] = user.firstName
    session["onboarding_completed"] = bool(getattr(user, "onboardingCompleted", False))
    session["remember_me"] = remember_me
    session.permanent = True


@auth_bp.get("/status")
def auth_status():
    return jsonify({"module": "auth", "status": "ok"})


@auth_bp.get("/login")
def login_page():
    return render_template("auth/login.html", standalone_auth=True)


@auth_bp.get("/register")
def register_page():
    return render_template("auth/register.html", standalone_auth=True)


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit(
    RATE_LIMITS["/forgot-password"],
    key_func=auth_rate_limit_key,
    methods=["POST"],
)
def forgot_password():
    message = "Password reset is disabled in this deployment."
    if _wants_json_response():
        return jsonify({"status": "disabled", "message": message}), 410
    flash(message, "warning")
    return redirect(url_for("auth.login_page"))


@auth_bp.post("/register")
@limiter.limit(RATE_LIMITS["/register"], key_func=auth_rate_limit_key)
def register():
    payload = request.get_json(silent=True) if request.is_json else request.form
    payload = payload or {}

    first_name = sanitize_input(payload.get("firstName"))
    surname = sanitize_input(payload.get("surname"))
    email = sanitize_input(payload.get("email")).lower()
    role = "employee"
    password = payload.get("password", "")
    confirm_password = payload.get("confirmPassword", "")

    errors: dict[str, str] = {}
    if not validate_name(first_name):
        errors["firstName"] = "First Name is required and must be at least 2 characters."
    if not validate_name(surname):
        errors["surname"] = "Surname is required and must be at least 2 characters."
    if not validate_email(email):
        errors["email"] = "Email Address must be valid."
    password_check = validate_password(password)
    if not password_check["valid"]:
        errors["password"] = password_check["errors"][0]
    if confirm_password != password:
        errors["confirmPassword"] = "Confirm Password must match Password."

    if errors:
        if _wants_json_response():
            return jsonify({"status": "error", "errors": errors}), 400
        flash("Please fix the highlighted registration fields.", "error")
        return redirect(url_for("auth.register_page"))

    existing = user_repository.get_user_by_email(email)
    if existing is not None:
        error_payload = {"email": "An account with this email already exists."}
        if _wants_json_response():
            return jsonify({"status": "error", "errors": error_payload}), 409
        flash(error_payload["email"], "error")
        return redirect(url_for("auth.register_page"))

    created_uid = str(uuid4())
    password_hash = generate_password_hash(password)

    user_repository.create_user(
        uid=created_uid,
        first_name=first_name,
        surname=surname,
        email=email,
        role=role,
        password_hash=password_hash,
    )
    audit_repository.create_log(
        user_id=created_uid,
        action="user_registered",
        details={"email": email, "role": role},
        request=request,
    )

    created_user = user_repository.get_user(created_uid)
    _create_session(created_user, remember_me=False)

    flash("Registration successful. Welcome to Folio!", "success")
    redirect_url = url_for("auth.dashboard")
    if _wants_json_response():
        return jsonify({"status": "ok", "redirect": redirect_url}), 200
    return redirect(redirect_url)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    _ = token
    message = "Password reset is disabled in this deployment."
    if _wants_json_response():
        return jsonify({"status": "disabled", "message": message}), 410
    flash(message, "warning")
    return redirect(url_for("auth.login_page"))


@auth_bp.post("/login")
@limiter.limit(
    RATE_LIMITS["/login"],
    key_func=auth_rate_limit_key,
)
def login():
    payload = request.get_json(silent=True) if request.is_json else request.form
    payload = payload or {}

    email = sanitize_input(payload.get("email")).lower()
    password = payload.get("password", "")
    remember_me = str(payload.get("rememberMe", "")).lower() in {
        "1",
        "true",
        "on",
        "yes",
    }

    client_key = f"{_client_key()}:{email or 'unknown'}"
    if _too_many_failed_attempts(client_key):
        message = "Too many attempts. Try again in 10 minutes."
        if _wants_json_response():
            return jsonify({"status": "error", "errors": {"auth": message}}), 429
        flash(message, "error")
        return redirect(url_for("auth.login_page"))

    if not validate_email(email) or not password:
        message = "Invalid email or password."
        _record_failed_attempt(client_key)
        if _wants_json_response():
            return jsonify({"status": "error", "errors": {"auth": message}}), 401
        flash(message, "error")
        return redirect(url_for("auth.login_page"))

    user = user_repository.get_user_by_email(email)
    if user is None:
        _record_failed_attempt(client_key)
        message = "Invalid email or password."
        if _wants_json_response():
            return jsonify({"status": "error", "errors": {"auth": message}}), 401
        flash(message, "error")
        return redirect(url_for("auth.login_page"))
    if not user.passwordHash:
        _record_failed_attempt(client_key)
        message = "This account requires a password reset before sign in."
        if _wants_json_response():
            return jsonify({"status": "error", "errors": {"auth": message}}), 403
        flash(message, "error")
        return redirect(url_for("auth.login_page"))
    if not check_password_hash(user.passwordHash, password):
        _record_failed_attempt(client_key)
        message = "Invalid email or password."
        if _wants_json_response():
            return jsonify({"status": "error", "errors": {"auth": message}}), 401
        flash(message, "error")
        return redirect(url_for("auth.login_page"))
    if user.disabled:
        _record_failed_attempt(client_key)
        message = "This account is disabled."
        if _wants_json_response():
            return jsonify({"status": "error", "errors": {"auth": message}}), 403
        flash(message, "error")
        return redirect(url_for("auth.login_page"))

    _clear_failed_attempts(client_key)
    _create_session(user, remember_me=remember_me)

    if remember_me:
        current_app.permanent_session_lifetime = timedelta(days=30)
    else:
        current_app.permanent_session_lifetime = timedelta(hours=8)

    redirect_url = url_for("auth.dashboard")
    audit_repository.create_log(
        user_id=user.id,
        action="user_login",
        details={"email": email},
        request=request,
    )
    if _wants_json_response():
        return jsonify({"status": "ok", "redirect": redirect_url}), 200
    return redirect(redirect_url)


@auth_bp.get("/logout")
def logout():
    uid = session.get("uid")
    email = session.get("email")
    audit_repository.create_log(
        user_id=uid,
        action="user_logout",
        details={"email": email},
        request=request,
    )
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth.login_page"))


@auth_bp.get("/dashboard")
@require_auth
def dashboard():
    return redirect(url_for("dashboard.home"))


@auth_bp.route("/settings", methods=["GET", "POST"])
@require_auth
def settings():
    uid = session.get("uid")
    user = user_repository.get_user(uid) if uid else None
    if user is None:
        flash("Unable to load your account settings.", "error")
        return redirect(url_for("auth.dashboard"))

    if request.method == "GET":
        initials = (f"{(user.firstName[:1] or '')}{(user.surname[:1] or '')}").upper()
        return render_template(
            "auth/account_settings.html",
            user=user,
            initials=initials or "U",
        )

    payload = request.get_json(silent=True) if request.is_json else request.form
    payload = payload or {}
    action = payload.get("action", "profile")

    if action == "profile":
        first_name = sanitize_input(payload.get("firstName"))
        surname = sanitize_input(payload.get("surname"))
        errors: dict[str, str] = {}
        if not validate_name(first_name):
            errors["firstName"] = "First Name is required and must be at least 2 characters."
        if not validate_name(surname):
            errors["surname"] = "Surname is required and must be at least 2 characters."
        if errors:
            return jsonify({"status": "error", "errors": errors}), 400

        user_repository.update_user(uid, {"firstName": first_name, "surname": surname})
        session["name"] = first_name
        return jsonify({"status": "ok", "message": "Profile updated successfully."}), 200

    if action == "password_reset":
        return (
            jsonify(
                {
                    "status": "disabled",
                    "message": "Password reset is disabled in this deployment.",
                }
            ),
            410,
        )

    return jsonify({"status": "error", "message": "Unsupported settings action."}), 400


@auth_bp.post("/set-language")
def set_language():
    payload = request.get_json(silent=True) or {}
    lang = payload.get("lang", "").strip().lower()
    supported_languages = current_app.config.get("SUPPORTED_LANGUAGES", ["en", "de"])

    if lang not in supported_languages:
        return jsonify({"status": "error", "message": "Unsupported language"}), 400

    session["lang"] = lang
    saved_to_profile = False

    user_id = session.get("uid")

    if user_id:
        try:
            user_repository.update_user(user_id, {"language": lang})
            saved_to_profile = True
        except Exception:
            saved_to_profile = False

    return jsonify({"status": "ok", "lang": lang, "savedToProfile": saved_to_profile}), 200


@auth_bp.post("/onboarding/complete")
@require_auth
def complete_onboarding():
    uid = session.get("uid")
    if not uid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    user_repository.update_user(uid, {"onboardingCompleted": True})
    session["onboarding_completed"] = True
    audit_repository.create_log(
        user_id=uid,
        action="onboarding_completed",
        details={},
        request=request,
    )
    return jsonify({"status": "ok"}), 200


@auth_bp.get("/translations/<lang>")
@require_auth
def translations(lang: str):
    return jsonify(get_locale_payload(lang))
