"""Storage service for Firebase Storage operations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import firebase as firebase_config

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "heic"}
MAX_RECEIPT_SIZE_BYTES = 15 * 1024 * 1024


class StorageUploadException(Exception):
    """Raised when receipt uploads fail validation or storage."""


def _extract_extension(filename: str) -> str:
    return Path(filename or "").suffix.lower().lstrip(".")


def _read_size(file_object) -> int:
    if getattr(file_object, "content_length", None):
        return int(file_object.content_length)

    stream = getattr(file_object, "stream", None)
    if stream is None:
        return 0

    current_position = stream.tell()
    stream.seek(0, 2)
    total_size = stream.tell()
    stream.seek(current_position)
    return int(total_size)


def upload_receipt_image(file_object, user_id: str, receipt_id: str) -> str:
    """Upload a receipt image and return a 1-year signed URL."""
    extension = _extract_extension(getattr(file_object, "filename", ""))
    if extension not in ALLOWED_EXTENSIONS:
        raise StorageUploadException(
            "Invalid file format. Allowed: jpg, jpeg, png, webp, heic."
        )

    file_size = _read_size(file_object)
    if file_size > MAX_RECEIPT_SIZE_BYTES:
        raise StorageUploadException("File exceeds 15MB limit.")

    if firebase_config.bucket is None:
        raise StorageUploadException("Storage bucket unavailable.")

    object_path = f"receipts/{user_id}/{receipt_id}/original.{extension}"
    blob = firebase_config.bucket.blob(object_path)

    try:
        file_object.stream.seek(0)
        blob.upload_from_file(
            file_object.stream,
            content_type=getattr(file_object, "mimetype", None),
        )
        expires_at = datetime.now(timezone.utc) + timedelta(days=365)
        try:
            return blob.generate_signed_url(
                expiration=expires_at,
                method="GET",
                version="v4",
            )
        except Exception:
            return blob.public_url
    except StorageUploadException:
        raise
    except Exception as exc:
        raise StorageUploadException("Unable to upload receipt image.") from exc
