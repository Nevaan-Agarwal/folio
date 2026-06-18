from __future__ import annotations

import json
import os
import time
from datetime import timedelta
from urllib import error, request as urllib_request

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

from config import firebase as firebase_config
from middleware.auth_middleware import require_auth
from middleware.rate_limiter import RATE_LIMITS, auth_rate_limit_key, limiter
from repositories import audit_repository, user_repository
from services import email_service
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


def _firebase_sign_in(email: str, password: str) -> dict:
    api_key = current_app.config.get("FIREBASE_WEB_API_KEY") or os.getenv(
        "FIREBASE_WEB_API_KEY", ""
    )
    if not api_key:
        raise RuntimeError("Missing FIREBASE_WEB_API_KEY")

    endpoint = (
        "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
        f"?key={api_key}"
    )
    payload = json.dumps(
        {"email": email, "password": password, "returnSecureToken": True}
    ).encode("utf-8")
    req = urllib_request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib_request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as http_error:
        body = http_error.read().decode("utf-8", errors="ignore")
        message = "Invalid email or password."
        if "INVALID_PASSWORD" in body or "EMAIL_NOT_FOUND" in body:
            raise ValueError(message) from http_error
        if "USER_DISABLED" in body:
            raise ValueError("This account is disabled.") from http_error
        raise ValueError(message) from http_error
    except error.URLError as exc:
        raise RuntimeError("Authentication service unavailable.") from exc


def _send_password_reset_email(email: str, reset_link: str) -> None:
    sendgrid_key = current_app.config.get("SENDGRID_API_KEY", "")
    from_email = current_app.config.get("SENDGRID_FROM_EMAIL", "")
    if not sendgrid_key or not from_email:
        return

    subject = "Reset your Folio password"
    plain_body = (
        "You requested a password reset for your Folio account.\n\n"
        f"Reset your password using this secure link:\n{reset_link}\n\n"
        "If you did not request this change, you can ignore this email."
    )
    html_body = f"""
    <div style="background:#0F1929;padding:24px;font-family:Inter,Arial,sans-serif;color:#F0EDE8;">
      <div style="max-width:640px;margin:0 auto;background:#1A2744;border:1px solid #2A3F6B;border-radius:16px;padding:24px;">
        <h1 style="margin:0 0 10px;color:#F5A623;font-family:'JetBrains Mono',monospace;">Folio</h1>
        <p style="margin:0 0 18px;color:#8A97B0;">Hospitality Expense Automation</p>
        <h2 style="margin:0 0 12px;font-size:20px;color:#F0EDE8;">Reset your Folio password</h2>
        <p style="margin:0 0 16px;color:#F0EDE8;">Click the button below to set a new password.</p>
        <a href="{reset_link}" style="display:inline-block;padding:12px 20px;border-radius:10px;background:#F5A623;color:#0F1929;font-weight:600;text-decoration:none;">
          Reset Password
        </a>
        <p style="margin:18px 0 0;color:#8A97B0;font-size:13px;">
          If you did not request this change, you can safely ignore this email.
        </p>
      </div>
    </div>
    """.strip()
    email_service.send_email_with_html(
        api_key=sendgrid_key,
        from_email=from_email,
        to_email=email,
        subject=subject,
        plain_text=plain_body,
        html_content=html_body,
    )


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
    if request.method == "GET":
        return render_template("auth/forgot_password.html", standalone_auth=True)

    payload = request.get_json(silent=True) if request.is_json else request.form
    payload = payload or {}
    email = sanitize_input(payload.get("email")).lower()

    # Always return the same success response to avoid account enumeration.
    success_message = "If that email exists, you'll receive a link shortly"
    if validate_email(email) and firebase_config.firebase_auth is not None:
        try:
            link = firebase_config.firebase_auth.generate_password_reset_link(email)
            _send_password_reset_email(email=email, reset_link=link)
            audit_repository.create_log(
                user_id=None,
                action="password_reset_requested",
                details={"email": email},
                request=request,
            )
        except Exception:
            pass

    if _wants_json_response():
        return jsonify({"status": "ok", "message": success_message}), 200

    flash(success_message, "success")
    return redirect(url_for("auth.forgot_password"))


@auth_bp.post("/register")
@limiter.limit(RATE_LIMITS["/register"], key_func=auth_rate_limit_key)
def register():
    payload = request.get_json(silent=True) if request.is_json else request.form
    payload = payload or {}

    first_name = sanitize_input(payload.get("firstName"))
    surname = sanitize_input(payload.get("surname"))
    email = sanitize_input(payload.get("email")).lower()
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

    if firebase_config.firebase_auth is None:
        message = "Authentication service unavailable."
        if _wants_json_response():
            return jsonify({"status": "error", "errors": {"auth": message}}), 503
        flash(message, "error")
        return redirect(url_for("auth.register_page"))

    try:
        created_user = firebase_config.firebase_auth.create_user(
            email=email,
            password=password,
            display_name=f"{first_name} {surname}".strip(),
        )
    except Exception as exc:
        exc_name = type(exc).__name__
        message = str(exc)
        if "EmailAlreadyExists" in exc_name or "already exists" in message.lower():
            error_payload = {"email": "An account with this email already exists."}
            if _wants_json_response():
                return jsonify({"status": "error", "errors": error_payload}), 409
            flash(error_payload["email"], "error")
            return redirect(url_for("auth.register_page"))

        if _wants_json_response():
            return jsonify({"status": "error", "errors": {"auth": message}}), 500
        flash("Registration failed. Please try again.", "error")
        return redirect(url_for("auth.register_page"))

    user_repository.create_user(
        uid=created_user.uid,
        first_name=first_name,
        surname=surname,
        email=email,
    )
    audit_repository.create_log(
        user_id=created_user.uid,
        action="user_registered",
        details={"email": email},
        request=request,
    )

    session["uid"] = created_user.uid
    session["email"] = email
    session["name"] = first_name
    session["role"] = "employee"
    session["lang"] = "en"

    flash("Registration successful. Welcome to Folio!", "success")
    redirect_url = url_for("auth.dashboard")
    if _wants_json_response():
        return jsonify({"status": "ok", "redirect": redirect_url}), 200
    return redirect(redirect_url)


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

    if firebase_config.firebase_auth is None:
        message = "Authentication service unavailable."
        if _wants_json_response():
            return jsonify({"status": "error", "errors": {"auth": message}}), 503
        flash(message, "error")
        return redirect(url_for("auth.login_page"))

    try:
        sign_in_payload = _firebase_sign_in(email=email, password=password)
        id_token = sign_in_payload.get("idToken")
        if not id_token:
            raise ValueError("Invalid email or password.")
        decoded_token = firebase_config.firebase_auth.verify_id_token(id_token)
    except ValueError as exc:
        _record_failed_attempt(client_key)
        if _wants_json_response():
            return jsonify({"status": "error", "errors": {"auth": str(exc)}}), 401
        flash(str(exc), "error")
        return redirect(url_for("auth.login_page"))
    except Exception:
        _record_failed_attempt(client_key)
        message = "Authentication service unavailable."
        if _wants_json_response():
            return jsonify({"status": "error", "errors": {"auth": message}}), 503
        flash(message, "error")
        return redirect(url_for("auth.login_page"))

    uid = decoded_token.get("uid")
    user = user_repository.get_user(uid) if uid else None
    if user is None:
        _record_failed_attempt(client_key)
        message = "User profile not found."
        if _wants_json_response():
            return jsonify({"status": "error", "errors": {"auth": message}}), 403
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
    session["uid"] = uid
    session["email"] = user.email
    session["role"] = user.role
    session["lang"] = user.language
    session["name"] = user.firstName
    session["remember_me"] = remember_me
    session.permanent = True

    if remember_me:
        current_app.permanent_session_lifetime = timedelta(days=30)
    else:
        current_app.permanent_session_lifetime = timedelta(hours=8)

    redirect_url = url_for("auth.dashboard")
    audit_repository.create_log(
        user_id=uid,
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
    return redirect(url_for("receipts.new_receipt"))


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
        message = "A password reset link has been sent to your email"
        if firebase_config.firebase_auth is not None:
            try:
                link = firebase_config.firebase_auth.generate_password_reset_link(user.email)
                _send_password_reset_email(email=user.email, reset_link=link)
            except Exception:
                pass
        return jsonify({"status": "ok", "message": message}), 200

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

    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header else ""
    user_id = session.get("uid")

    if user_id and firebase_config.db is not None:
        try:
            firebase_config.db.collection("users").document(user_id).set(
                {"language": lang}, merge=True
            )
            saved_to_profile = True
        except Exception:
            saved_to_profile = False
    elif token and firebase_config.firebase_auth is not None and firebase_config.db is not None:
        try:
            decoded = firebase_config.firebase_auth.verify_id_token(token)
            token_uid = decoded.get("uid")
            if token_uid:
                firebase_config.db.collection("users").document(token_uid).set(
                    {"language": lang}, merge=True
                )
                saved_to_profile = True
        except Exception:
            saved_to_profile = False

    return jsonify({"status": "ok", "lang": lang, "savedToProfile": saved_to_profile}), 200


@auth_bp.get("/translations/<lang>")
@require_auth
def translations(lang: str):
    return jsonify(get_locale_payload(lang))
