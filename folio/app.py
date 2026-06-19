"""Application entry point for Folio."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from config import firebase as firebase_config
from config.settings import get_config
from middleware.rate_limiter import create_limiter
from routes.admin import admin_bp
from routes.api import api_bp
from routes.archive import archive_bp
from routes.auth import auth_bp
from routes.documents import documents_bp
from routes.forms import forms_bp
from routes.pdf import pdf_bp
from routes.receipts import receipts_bp
from utils.db_bootstrap import run_smoke_transaction, run_sql_file
from utils.helpers import get_current_language, translate


def create_app(config_name: str | None = None) -> Flask:
    """Application factory for Flask."""
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(get_config(config_name))
    create_limiter(app)
    app.jinja_env.globals["t"] = translate
    app.jinja_env.globals["current_lang"] = get_current_language

    firebase_config.init_firebase(app)

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(receipts_bp, url_prefix="/receipts")
    app.register_blueprint(forms_bp, url_prefix="/forms")
    app.register_blueprint(documents_bp, url_prefix="")
    app.register_blueprint(pdf_bp, url_prefix="/pdf")
    app.register_blueprint(archive_bp, url_prefix="/archive")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(api_bp, url_prefix="/api")

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

    @app.get("/dashboard")
    def dashboard_alias():
        return redirect(url_for("auth.dashboard"))

    @app.route("/settings", methods=["GET", "POST"])
    def settings_alias():
        code = 307 if request.method == "POST" else 302
        return redirect(url_for("auth.settings"), code=code)

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password_alias():
        code = 307 if request.method == "POST" else 302
        return redirect(url_for("auth.forgot_password"), code=code)

    @app.get("/health")
    def health():
        now = datetime.now(timezone.utc).isoformat()
        try:
            if firebase_config.db is None:
                raise RuntimeError("Database client unavailable")
            doc = (
                firebase_config.db.collection("_healthchecks")
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

    @app.cli.command("db-init")
    @click.option(
        "--schema",
        "schema_path",
        default="scripts/postgres_schema.sql",
        show_default=True,
        help="Path to SQL schema file",
    )
    def db_init_command(schema_path: str):
        schema = Path(schema_path)
        if not schema.exists():
            raise click.ClickException(f"Schema file not found: {schema}")
        database_url = app.config.get("DATABASE_URL", "sqlite:///folio.db")
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

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000)
