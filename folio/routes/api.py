import json

from flask import Blueprint, g, jsonify, render_template, request, url_for

from config import database as database_config
from middleware.auth_middleware import require_auth
from repositories import audit_repository

api_bp = Blueprint("api", __name__)
search_bp = Blueprint("search", __name__)


@api_bp.get("/status")
@require_auth
def api_status():
    return jsonify({"module": "api", "status": "ok"})


def _notification_payload(item: audit_repository.AuditLogModel) -> dict:
    details = item.details or {}
    action = item.action
    notif_type = "info"
    message = "New notification"
    icon = "info"
    link = "/auth/dashboard"

    receipt_id = details.get("receiptId", "")
    document_id = details.get("documentId", "")

    if action == "ocr_completed":
        notif_type = "receipt_scanned"
        confidence = details.get("confidence")
        try:
            confidence_text = f"{float(confidence):.1f}"
        except (TypeError, ValueError):
            confidence_text = "0.0"
        message = f"Receipt scanned successfully ({confidence_text}% confidence)"
        icon = "receipt"
        if receipt_id:
            link = f"/receipts/{receipt_id}/review"
    elif action == "ai_processed":
        notif_type = "form_ready"
        message = "Your form is ready to review"
        icon = "form"
        if receipt_id:
            link = f"/receipts/{receipt_id}/review"
    elif action == "pdf_generated":
        notif_type = "pdf_generated"
        message = "Your PDF has been generated"
        icon = "pdf"
        if document_id:
            link = f"/archive/{document_id}"
    elif action in {"email_sent", "email_resent"}:
        notif_type = "email_sent"
        target = details.get("email") or details.get("toEmail") or "your email"
        message = f"Expense report emailed to {target}"
        icon = "email"
        if document_id:
            link = f"/archive/{document_id}"
    elif action == "ocr_failed":
        notif_type = "ocr_failed"
        message = "Receipt scan failed — please retake"
        icon = "error"
        link = "/receipts/new"
    elif action == "manual_review_required":
        notif_type = "manual_review_required"
        message = "Some fields need your attention"
        icon = "review"
        if receipt_id:
            link = f"/receipts/{receipt_id}/review"

    return {
        "id": item.id,
        "type": notif_type,
        "message": message,
        "timestamp": item.timestamp,
        "isRead": str(g.user.get("uid", "")) in (item.readBy or []),
        "link": link,
        "icon": icon,
    }


@api_bp.get("/notifications")
@require_auth
def notifications():
    user_id = g.user.get("uid")
    items = audit_repository.get_user_notifications(user_id=user_id, limit=20)
    payload = [_notification_payload(item) for item in items]
    return jsonify(payload)


@api_bp.get("/notifications/count")
@require_auth
def notification_count():
    user_id = g.user.get("uid")
    unread = audit_repository.get_unread_notification_count(user_id=user_id)
    return jsonify({"unreadCount": unread})


@api_bp.post("/notifications/<notification_id>/read")
@require_auth
def notification_mark_read(notification_id: str):
    user_id = g.user.get("uid")
    updated = audit_repository.mark_notification_read(
        user_id=user_id,
        notification_id=notification_id,
    )
    if not updated:
        return jsonify({"success": False, "error": "Notification not found"}), 404
    return jsonify({"success": True})


@api_bp.post("/notifications/read-all")
@require_auth
def notification_mark_all_read():
    user_id = g.user.get("uid")
    updated_count = audit_repository.mark_all_notifications_read(user_id=user_id)
    return jsonify({"success": True, "updated": updated_count})


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_str(value) -> str:
    return str(value or "")


def _load_filters(raw_filters: str | None) -> dict:
    if not raw_filters:
        return {}
    try:
        parsed = json.loads(raw_filters)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _fetch_combined_documents(user_id: str, role: str, query: str) -> list[tuple[str, dict]]:
    if database_config.db is None:
        return []
    collection = database_config.db.collection("combined_documents")
    if role != "admin":
        docs = collection.where("userId", "==", user_id).stream()
        rows = [(doc.id, doc.to_dict() or {}) for doc in docs]
        rows.sort(key=lambda item: item[1].get("createdAt", ""), reverse=True)
        return rows[:500]

    docs = collection.stream()
    rows = [(doc.id, doc.to_dict() or {}) for doc in docs]
    rows.sort(key=lambda item: item[1].get("createdAt", ""), reverse=True)
    if len(rows) <= 500:
        return rows

    # Basic indexed fallback for large admin datasets.
    normalized_query = (query or "").strip()
    if normalized_query:
        ranged = (
            collection.where("merchant", ">=", normalized_query)
            .where("merchant", "<=", f"{normalized_query}\uf8ff")
            .stream()
        )
        ranged_rows = [(doc.id, doc.to_dict() or {}) for doc in ranged]
        if ranged_rows:
            ranged_rows.sort(key=lambda item: item[1].get("createdAt", ""), reverse=True)
            return ranged_rows[:500]

    return rows[:500]


def _build_search_result(doc_id: str, payload: dict) -> dict:
    form_id = payload.get("formId", "")
    receipt_id = payload.get("receiptId", "")
    form = {}
    receipt = {}
    if database_config.db is not None and form_id:
        form_doc = database_config.db.collection("forms").document(form_id).get()
        if form_doc.exists:
            form = form_doc.to_dict() or {}
    if database_config.db is not None and receipt_id:
        receipt_doc = database_config.db.collection("receipts").document(receipt_id).get()
        if receipt_doc.exists:
            receipt = receipt_doc.to_dict() or {}

    merchant = payload.get("merchant") or form.get("merchant") or receipt.get("merchant") or "-"
    date = form.get("date") or form.get("dateOfHospitality") or _safe_str(payload.get("createdAt"))[:10]
    category = payload.get("category") or form.get("expenseCategory") or "Other"
    host = payload.get("host") or form.get("host") or ""
    occasion = payload.get("occasion") or form.get("occasion") or ""
    receipt_number = form.get("receiptNumber") or ""
    amount = _to_float(payload.get("totalAmount") or form.get("totalAmount") or receipt.get("total"))
    status = receipt.get("processingStatus") or payload.get("status") or "processing"
    document_id = payload.get("id", doc_id)

    return {
        "id": document_id,
        "merchant": merchant,
        "date": date,
        "amount": round(amount, 2),
        "category": category,
        "status": status,
        "thumbnail": (
            url_for("receipts.receipt_image", receipt_id=receipt_id)
            if receipt_id
            else ""
        ),
        "occasion": occasion,
        "host": host,
        "expenseCategory": category,
        "receiptNumber": receipt_number,
        "link": url_for("archive.archive_document_detail", document_id=document_id),
    }


def _matches_search(item: dict, query: str, filters: dict) -> bool:
    normalized_query = (query or "").strip().lower()
    if normalized_query:
        haystacks = [
            _safe_str(item.get("merchant")).lower(),
            _safe_str(item.get("occasion")).lower(),
            _safe_str(item.get("host")).lower(),
            _safe_str(item.get("date")).lower(),
            _safe_str(item.get("receiptNumber")).lower(),
        ]
        category_exact = _safe_str(item.get("expenseCategory")).lower() == normalized_query
        if not category_exact and not any(normalized_query in field for field in haystacks):
            return False

    category_filter = _safe_str(filters.get("expenseCategory") or filters.get("category")).strip()
    if category_filter and _safe_str(item.get("expenseCategory")) != category_filter:
        return False
    status_filter = _safe_str(filters.get("status")).strip().lower()
    if status_filter and _safe_str(item.get("status")).lower() != status_filter:
        return False
    date_filter = _safe_str(filters.get("date")).strip()
    if date_filter and date_filter not in _safe_str(item.get("date")):
        return False
    merchant_filter = _safe_str(filters.get("merchant")).strip().lower()
    if merchant_filter and merchant_filter not in _safe_str(item.get("merchant")).lower():
        return False
    return True


def perform_search(user_id: str, role: str, query: str, filters: dict, limit: int = 20) -> dict:
    rows = _fetch_combined_documents(user_id=user_id, role=role, query=query)
    hydrated = [_build_search_result(doc_id, payload) for doc_id, payload in rows]
    filtered = [item for item in hydrated if _matches_search(item, query=query, filters=filters)]
    try:
        requested_limit = int(limit)
    except (TypeError, ValueError):
        requested_limit = 20
    max_limit = max(1, min(requested_limit, 100))
    sliced = filtered[:max_limit]
    return {
        "results": [
            {
                "id": item["id"],
                "merchant": item["merchant"],
                "date": item["date"],
                "amount": item["amount"],
                "category": item["category"],
                "status": item["status"],
                "thumbnail": item["thumbnail"],
                "link": item["link"],
            }
            for item in sliced
        ],
        "total": len(filtered),
        "query": query,
    }


@api_bp.get("/search")
@require_auth
def search_api():
    query = request.args.get("q", "").strip()
    filters = _load_filters(request.args.get("filters"))
    limit = request.args.get("limit", 20)
    payload = perform_search(
        user_id=g.user.get("uid"),
        role=g.user.get("role", "employee"),
        query=query,
        filters=filters,
        limit=limit,
    )
    return jsonify(payload)


@search_bp.get("/search")
@require_auth
def search_page():
    query = request.args.get("q", "").strip()
    filters = _load_filters(request.args.get("filters"))
    payload = perform_search(
        user_id=g.user.get("uid"),
        role=g.user.get("role", "employee"),
        query=query,
        filters=filters,
        limit=500,
    )
    return render_template(
        "archive/search_results.html",
        query=query,
        total=payload["total"],
        results=payload["results"],
    )
