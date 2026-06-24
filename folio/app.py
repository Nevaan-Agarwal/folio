"""Application entry point for Folio."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from config import database as database_config
from config.settings import get_config
from middleware.rate_limiter import create_limiter
from repositories import audit_repository, user_repository
from routes.admin import admin_bp
from routes.api import api_bp, search_bp
from routes.archive import archive_bp
from routes.auth import auth_bp
from routes.dashboard import dashboard_bp
from routes.documents import documents_bp
from routes.forms import forms_bp
from routes.pdf import pdf_bp
from routes.receipts import receipts_bp
from utils.db_bootstrap import default_schema_path, run_smoke_transaction, run_sql_file
from utils.helpers import get_current_language, translate


def create_app(config_name: str | None = None) -> Flask:
    """Application factory for Flask."""
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(get_config(config_name))
    create_limiter(app)
    app.jinja_env.globals["t"] = translate
    app.jinja_env.globals["current_lang"] = get_current_language

    database_config.init_database(app)

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(dashboard_bp, url_prefix="")
    app.register_blueprint(receipts_bp, url_prefix="/receipts")
    app.register_blueprint(forms_bp, url_prefix="/forms")
    app.register_blueprint(documents_bp, url_prefix="")
    app.register_blueprint(pdf_bp, url_prefix="/pdf")
    app.register_blueprint(archive_bp, url_prefix="/archive")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(search_bp, url_prefix="")

    @app.before_request
    def configure_session():
        session.permanent = True
        if session.get("remember_me"):
            app.permanent_session_lifetime = timedelta(days=30)
        else:
            app.permanent_session_lifetime = app.config["PERMANENT_SESSION_LIFETIME"]
        if "lang" not in session:
            session["lang"] = app.config.get("DEFAULT_LANGUAGE", "en")

    @app.get("/")
    def index():
        return render_template("base.html")

    @app.get("/login")
    def login_alias():
        return redirect(url_for("auth.login_page"))

    @app.get("/logout")
    def logout_alias():
        return redirect(url_for("auth.logout"))

    @app.route("/settings", methods=["GET", "POST"])
    def settings_alias():
        code = 307 if request.method == "POST" else 302
        return redirect(url_for("auth.settings"), code=code)

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password_alias():
        return redirect(url_for("auth.login_page"))

    @app.get("/health")
    def health():
        now = datetime.now(timezone.utc).isoformat()
        try:
            if database_config.db is None:
                raise RuntimeError("Database client unavailable")
            doc = (
                database_config.db.collection("_healthchecks")
                .document("db-connection")
                .get()
            )
            _ = doc.exists
            return jsonify(
                {"status": "ok", "database": "connected", "timestamp": now}
            )
        except Exception:
            return (
                jsonify(
                    {"status": "error", "database": "unavailable", "timestamp": now}
                ),
                503,
            )

    @app.errorhandler(429)
    def handle_rate_limit(error):
        retry_after = getattr(error, "retry_after", None)
        try:
            retry_after = int(retry_after) if retry_after is not None else 0
        except (TypeError, ValueError):
            retry_after = 0
        return (
            jsonify(
                {
                    "error": "Rate limit exceeded",
                    "retryAfter": retry_after,
                    "code": 429,
                }
            ),
            429,
        )

    @app.errorhandler(403)
    def handle_forbidden(_error):
        accepts_json = (
            request.path.startswith("/api")
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in (request.headers.get("Accept") or "")
        )
        if accepts_json:
            return jsonify({"success": False, "error": "Forbidden"}), 403
        return render_template("errors/403.html"), 403

    @app.cli.command("db-init")
    @click.option(
        "--schema",
        "schema_path",
        default=None,
        show_default="database-specific",
        help="Path to SQL schema file",
    )
    def db_init_command(schema_path: str | None):
        database_url = app.config.get("DATABASE_URL", "sqlite:///folio.db")
        selected_schema_path = schema_path or default_schema_path(database_url)
        schema = Path(selected_schema_path)
        if not schema.exists():
            raise click.ClickException(f"Schema file not found: {schema}")
        try:
            run_sql_file(database_url=database_url, sql_file_path=str(schema))
            click.echo(f"Schema applied successfully to {database_url}")
        except Exception as exc:
            raise click.ClickException(f"Failed to apply schema: {exc}") from exc

    @app.cli.command("db-smoke")
    @click.option(
        "--user-id",
        default="smoke_user_001",
        show_default=True,
        help="User ID used for smoke transaction",
    )
    def db_smoke_command(user_id: str):
        database_url = app.config.get("DATABASE_URL", "sqlite:///folio.db")
        try:
            result = run_smoke_transaction(database_url=database_url, user_id=user_id)
            click.echo(
                "Smoke transaction succeeded.\n"
                f"User row: {result['user']}\n"
                f"Audit row: {result['audit']}"
            )
        except Exception as exc:
            raise click.ClickException(f"Smoke transaction failed: {exc}") from exc

    @app.cli.command("make-admin")
    @click.option(
        "--email",
        required=True,
        help="Email address of the user to promote to admin.",
    )
    @click.option(
        "--dry-run",
        is_flag=True,
        default=False,
        help="Validate target user without applying changes.",
    )
    def make_admin_command(email: str, dry_run: bool):
        normalized_email = (email or "").strip().lower()
        if not normalized_email:
            raise click.ClickException("A valid --email value is required.")

        target_user = user_repository.get_user_by_email(normalized_email)
        if target_user is None:
            raise click.ClickException(
                f"User not found for email: {normalized_email}"
            )
        if target_user.disabled:
            raise click.ClickException(
                f"User {normalized_email} is deactivated. Reactivate before promotion."
            )
        if target_user.role == "admin":
            click.echo(f"User {normalized_email} is already an admin.")
            return
        if target_user.role != "employee":
            raise click.ClickException(
                f"User {normalized_email} has unsupported role: {target_user.role}"
            )

        if dry_run:
            click.echo(
                f"[dry-run] User {normalized_email} would be promoted to admin."
            )
            return

        user_repository.update_user(target_user.id, {"role": "admin"})
        audit_repository.create_log(
            user_id="system_cli",
            action="user_promoted_to_admin",
            details={
                "targetUser": target_user.id,
                "targetEmail": normalized_email,
                "previousRole": "employee",
                "newRole": "admin",
                "source": "cli_make_admin",
            },
        )
        click.echo(
            f"User {normalized_email} promoted to admin successfully."
        )

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000)
