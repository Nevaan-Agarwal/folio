"""Combined document repository for SQL-backed document records."""

from __future__ import annotations

from datetime import datetime, timezone

from config import database as database_config
from models.document import CombinedDocumentModel
from repositories import audit_repository


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
        emailError=data.get("emailError"),
        userEmail=data.get("userEmail", ""),
    )


def get_document(doc_id: str) -> CombinedDocumentModel | None:
    if database_config.db is None:
        return None
    doc = database_config.db.collection("combined_documents").document(doc_id).get()
    if not doc.exists:
        return None
    return _to_combined_document(doc.id, doc.to_dict())


def update_email_status(doc_id: str, status: str, message_id: str | None) -> None:
    if database_config.db is None:
        return
    payload = {
        "emailDeliveryStatus": status,
        "emailMessageId": message_id,
        "emailSent": True,
        "emailSentAt": datetime.now(timezone.utc).isoformat(),
        "emailError": None,
    }
    try:
        database_config.db.collection("combined_documents").document(doc_id).set(payload, merge=True)
        audit_repository.create_log(
            user_id="system",
            action="db_transaction",
            details={
                "status": "success",
                "operation": "update",
                "collection": "combined_documents",
                "docId": doc_id,
            },
        )
    except Exception as exc:
        audit_repository.create_log(
            user_id="system",
            action="db_transaction",
            details={
                "status": "failed",
                "operation": "update",
                "collection": "combined_documents",
                "docId": doc_id,
                "error": str(exc),
            },
        )
        raise


def save_document(document_data: dict) -> str:
    """Backward-compatible helper for legacy callers."""
    user_id = str((document_data or {}).get("userId") or "system")
    try:
        ref = database_config.db.collection("combined_documents").document()
        ref.set(document_data)
        audit_repository.create_log(
            user_id=user_id,
            action="db_transaction",
            details={
                "status": "success",
                "operation": "create",
                "collection": "combined_documents",
                "docId": ref.id,
            },
        )
        return ref.id
    except Exception as exc:
        audit_repository.create_log(
            user_id=user_id,
            action="db_transaction",
            details={
                "status": "failed",
                "operation": "create",
                "collection": "combined_documents",
                "docId": "",
                "error": str(exc),
            },
        )
        raise


def delete_document(doc_id: str) -> None:
    if database_config.db is None:
        return
    try:
        database_config.db.collection("combined_documents").document(doc_id).delete()
        audit_repository.create_log(
            user_id="system",
            action="db_transaction",
            details={
                "status": "success",
                "operation": "delete",
                "collection": "combined_documents",
                "docId": doc_id,
            },
        )
    except Exception as exc:
        audit_repository.create_log(
            user_id="system",
            action="db_transaction",
            details={
                "status": "failed",
                "operation": "delete",
                "collection": "combined_documents",
                "docId": doc_id,
                "error": str(exc),
            },
        )
        raise
