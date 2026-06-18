"""Audit repository for immutable event logs."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from config import firebase as firebase_config

ALLOWED_ACTIONS = {
    "user_registered",
    "user_login",
    "user_logout",
    "password_reset_requested",
    "receipt_uploaded",
    "ocr_completed",
    "ai_processed",
    "form_approved",
    "form_rejected",
    "pdf_generated",
    "email_sent",
    "user_promoted_to_admin",
    "user_demoted",
    "user_deactivated",
    "admin_export_csv",
    "document_downloaded",
    "email_resent",
}


@dataclass
class AuditLogModel:
    id: str
    userId: str
    action: str
    timestamp: str
    details: dict[str, Any]
    ipAddress: str
    userAgent: str
    sessionId: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_ip(request_obj) -> str:
    if request_obj is None:
        return ""
    forwarded = request_obj.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request_obj.remote_addr or ""


def _to_model(doc_id: str, payload: dict | None) -> AuditLogModel | None:
    data = payload or {}
    action = data.get("action", "")
    if not action:
        return None
    return AuditLogModel(
        id=doc_id,
        userId=str(data.get("userId", "")),
        action=str(action),
        timestamp=str(data.get("timestamp", "")),
        details=data.get("details", {}) or {},
        ipAddress=str(data.get("ipAddress", "")),
        userAgent=str(data.get("userAgent", "")),
        sessionId=str(data.get("sessionId", "")),
    )


def _write_audit_log(payload: dict) -> None:
    if firebase_config.db is None:
        return
    firebase_config.db.collection("auditLogs").add(payload)


def create_log(user_id, action: str, details: dict | None = None, request=None) -> None:
    """
    Fire-and-forget audit logging.

    This function never blocks the main request thread.
    """
    if action not in ALLOWED_ACTIONS:
        return

    payload = {
        "userId": str(user_id or ""),
        "action": action,
        "timestamp": _now_iso(),
        "details": details or {},
        "ipAddress": _extract_ip(request),
        "userAgent": request.headers.get("User-Agent", "") if request else "",
        "sessionId": request.cookies.get("session", "") if request else "",
    }
    try:
        threading.Thread(target=_write_audit_log, args=(payload,), daemon=True).start()
    except Exception:
        # Audit logging must never fail the main flow.
        return


def get_user_logs(user_id: str, limit: int = 50) -> list[AuditLogModel]:
    if firebase_config.db is None:
        return []
    models: list[AuditLogModel] = []
    docs = firebase_config.db.collection("auditLogs").where("userId", "==", str(user_id)).stream()
    for doc in docs:
        model = _to_model(doc.id, doc.to_dict())
        if model:
            models.append(model)
    models.sort(key=lambda item: item.timestamp or "", reverse=True)
    return models[: max(1, int(limit))]


def get_all_logs(
    limit: int = 100, action_filter: str | None = None, requester_role: str = "employee"
) -> list[AuditLogModel]:
    if requester_role != "admin" or firebase_config.db is None:
        return []
    docs = firebase_config.db.collection("auditLogs").stream()
    models: list[AuditLogModel] = []
    for doc in docs:
        model = _to_model(doc.id, doc.to_dict())
        if not model:
            continue
        if action_filter and model.action != action_filter:
            continue
        models.append(model)
    models.sort(key=lambda item: item.timestamp or "", reverse=True)
    return models[: max(1, int(limit))]


def log_event(event: dict) -> None:
    """
    Backwards-compatible wrapper for older call sites.
    """
    payload = dict(event or {})
    action = str(payload.get("action", "")).strip()
    user_id = payload.get("userId") or payload.get("uid") or payload.get("performedBy")
    reserved = {
        "action",
        "userId",
        "uid",
        "performedBy",
        "timestamp",
        "ipAddress",
        "userAgent",
        "sessionId",
    }
    details = {key: value for key, value in payload.items() if key not in reserved}
    create_log(user_id=user_id, action=action, details=details)
