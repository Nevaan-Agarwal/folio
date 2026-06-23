"""Database and local file storage bootstrap."""

from __future__ import annotations

import os
from pathlib import Path

from config.document_store import init_document_store

db = None
bucket = None


class LocalBlob:
    def __init__(self, root: Path, relative_path: str):
        self._root = root
        self.path = str(relative_path).replace("\\", "/")
        self._absolute_path = (root / self.path).resolve()

    @property
    def public_url(self) -> str:
        return self._absolute_path.as_uri()

    def upload_from_file(self, file_stream, content_type=None):
        self._absolute_path.parent.mkdir(parents=True, exist_ok=True)
        file_stream.seek(0)
        with self._absolute_path.open("wb") as handle:
            handle.write(file_stream.read())

    def upload_from_string(self, data: bytes, content_type=None):
        self._absolute_path.parent.mkdir(parents=True, exist_ok=True)
        payload = data if isinstance(data, (bytes, bytearray)) else str(data).encode("utf-8")
        with self._absolute_path.open("wb") as handle:
            handle.write(payload)

    def download_as_bytes(self) -> bytes:
        return self._absolute_path.read_bytes()

    def generate_signed_url(self, expiration=None, method="GET", version="v4"):
        return self.public_url

    def delete(self):
        if self._absolute_path.exists():
            self._absolute_path.unlink()


class LocalBucket:
    def __init__(self, root: Path):
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self.name = str(root)

    def blob(self, relative_path: str) -> LocalBlob:
        return LocalBlob(self._root, relative_path)


def init_database(app=None) -> None:
    """Initialize PostgreSQL-backed document store and local file bucket."""
    global db, bucket

    if app is not None:
        database_url = app.config.get("DATABASE_URL")
        if database_url:
            os.environ.setdefault("DATABASE_URL", str(database_url))
        storage_root = app.config.get("STORAGE_ROOT")
        if storage_root:
            os.environ.setdefault("STORAGE_ROOT", str(storage_root))

    database_url = os.getenv("DATABASE_URL", "").strip()
    is_testing = bool(app is not None and app.config.get("TESTING"))
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is required. Set it to a PostgreSQL URL "
            "(postgresql://... or postgres://...)."
        )
    if not is_testing and not (
        database_url.startswith("postgresql://")
        or database_url.startswith("postgres://")
    ):
        raise RuntimeError(
            "DATABASE_URL must be PostgreSQL in non-testing environments "
            "(postgresql://... or postgres://...)."
        )

    storage_root = Path(os.getenv("STORAGE_ROOT", "storage")).resolve()
    db = init_document_store(database_url)
    bucket = LocalBucket(storage_root)
