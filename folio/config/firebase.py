"""Firebase singleton initialization and shared clients."""

from __future__ import annotations

import os
from typing import Any

from firebase_admin import auth, credentials, firestore, get_app, initialize_app, storage

db = None
bucket = None
firebase_auth = None


def _validate_firebase_env(app) -> None:
    """Log missing Firebase env configuration for easier deployment debugging."""
    if app is None:
        return

    credentials_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "").strip()
    storage_bucket = os.getenv("FIREBASE_STORAGE_BUCKET", "").strip()
    project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()

    missing_keys = []
    if not credentials_path:
        missing_keys.append("FIREBASE_CREDENTIALS_PATH")
    if not storage_bucket:
        missing_keys.append("FIREBASE_STORAGE_BUCKET")

    if missing_keys:
        app.logger.warning("Missing Firebase config keys: %s", ", ".join(missing_keys))

    if credentials_path and not os.path.exists(credentials_path):
        app.logger.warning(
            "FIREBASE_CREDENTIALS_PATH points to missing file: %s",
            credentials_path,
        )

    if not project_id:
        app.logger.warning(
            "FIREBASE_PROJECT_ID is empty; set it or provide a credentials file with project_id."
        )


def _build_options() -> dict[str, Any]:
    options: dict[str, Any] = {}

    storage_bucket = os.getenv("FIREBASE_STORAGE_BUCKET", "").strip()
    project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()

    if storage_bucket:
        options["storageBucket"] = storage_bucket
    if project_id:
        options["projectId"] = project_id
    return options


def init_firebase(app=None) -> None:
    """Initialize Firebase Admin SDK exactly once and expose shared clients."""
    global db, bucket, firebase_auth

    if app is not None:
        for key in (
            "FIREBASE_CREDENTIALS_PATH",
            "FIREBASE_STORAGE_BUCKET",
            "FIREBASE_PROJECT_ID",
        ):
            value = app.config.get(key)
            if value:
                os.environ.setdefault(key, str(value))
        _validate_firebase_env(app)

    try:
        try:
            get_app()
        except ValueError:
            credentials_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "").strip()
            options = _build_options()
            app_options = options or None

            if credentials_path:
                credential = credentials.Certificate(credentials_path)
                initialize_app(credential, app_options)
            else:
                initialize_app(options=app_options)

        db = firestore.client()
        bucket = storage.bucket()
        firebase_auth = auth
    except Exception as exc:
        db = None
        bucket = None
        firebase_auth = None
        if app is not None:
            app.logger.warning("Firebase initialization unavailable: %s", exc)
