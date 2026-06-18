"""Combined document repository for Firestore operations."""

from __future__ import annotations

from datetime import datetime, timezone

from config import firebase as firebase_config
from models.document import CombinedDocumentModel


def _parse_datetime(value):
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return value if isinstance(value, datetime) else None


def _to_combined_document(doc_id: str, data: dict | None) -> CombinedDocumentModel | None:
    if not data:
        return None
    return CombinedDocumentModel(
        id=doc_id,
        formId=data.get("formId", ""),
        receiptId=data.get("receiptId", ""),
        userId=data.get("userId", ""),
        filePath=data.get("filePath", ""),
        downloadUrl=data.get("downloadUrl", ""),
        createdAt=_parse_datetime(data.get("createdAt")),
        emailSent=bool(data.get("emailSent", False)),
        emailSentAt=data.get("emailSentAt"),
        emailMessageId=data.get("emailMessageId"),
        emailDeliveryStatus=data.get("emailDeliveryStatus", "pending"),
        userEmail=data.get("userEmail", ""),
    )


def get_document(doc_id: str) -> CombinedDocumentModel | None:
    if firebase_config.db is None:
        return None
    doc = firebase_config.db.collection("combined_documents").document(doc_id).get()
    if not doc.exists:
        return None
    return _to_combined_document(doc.id, doc.to_dict())


def update_email_status(doc_id: str, status: str, message_id: str | None) -> None:
    if firebase_config.db is None:
        return
    payload = {
        "emailDeliveryStatus": status,
        "emailMessageId": message_id,
        "emailSent": True,
        "emailSentAt": datetime.now(timezone.utc).isoformat(),
    }
    firebase_config.db.collection("combined_documents").document(doc_id).set(payload, merge=True)


def save_document(document_data: dict) -> str:
    """Backward-compatible helper for legacy callers."""
    ref = firebase_config.db.collection("combined_documents").document()
    ref.set(document_data)
    return ref.id
