from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from flask import Blueprint, g, jsonify, render_template, request
from firebase_admin import firestore

from config import firebase as firebase_config
from middleware.auth_middleware import require_auth
from repositories import combined_document_repository, form_repository, receipt_repository

archive_bp = Blueprint("archive", __name__)

ALLOWED_CATEGORIES = {
    "Restaurant",
    "Business Meal",
    "Client Meeting",
    "Travel",
    "Hotel",
    "Transportation",
    "Office Supplies",
    "Entertainment",
    "Training",
    "Other",
}


def _to_iso_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date().isoformat()
    except ValueError:
        return None


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _extract_submission(doc) -> dict:
    if firebase_config.db is None:
        return {}
    payload = doc.to_dict() or {}
    receipt_id = payload.get("receiptId")
    form_id = payload.get("formId")
    receipt = {}
    form = {}
    if receipt_id:
        receipt_doc = firebase_config.db.collection("receipts").document(receipt_id).get()
        if receipt_doc.exists:
            receipt = receipt_doc.to_dict() or {}
    if form_id:
        form_doc = firebase_config.db.collection("forms").document(form_id).get()
        if form_doc.exists:
            form = form_doc.to_dict() or {}
    date_value = (
        form.get("date")
        or form.get("dateOfHospitality")
        or payload.get("createdAt", "")[:10]
    )
    return {
        "id": doc.id,
        "documentId": payload.get("id", doc.id),
        "receiptId": receipt_id or "",
        "formId": form_id or "",
        "merchant": form.get("merchant") or receipt.get("merchant") or "Unknown Merchant",
        "date": _to_iso_date(date_value) or "",
        "category": form.get("expenseCategory") or "Other",
        "occasion": form.get("occasion") or "",
        "totalAmount": _to_float(form.get("totalAmount") or receipt.get("total")),
        "status": receipt.get("processingStatus", "processing"),
        "thumbnailUrl": receipt.get("imageUrl", ""),
        "pdfUrl": payload.get("downloadUrl", ""),
        "createdAt": payload.get("createdAt", ""),
        "userId": payload.get("userId", ""),
    }


def _event_timestamp(event: dict) -> str:
    ts = event.get("timestamp")
    if not ts:
        return ""
    return str(ts)


def _get_audit_timeline(document_id: str, form_id: str, receipt_id: str, user_id: str) -> list[dict]:
    if firebase_config.db is None:
        return []
    events = []
    for doc in (
        firebase_config.db.collection("audit_logs")
        .where("uid", "==", user_id)
        .stream()
    ):
        payload = doc.to_dict() or {}
        details = payload.get("details") or {}
        if (
            payload.get("documentId") == document_id
            or payload.get("formId") == form_id
            or payload.get("receiptId") == receipt_id
            or details.get("formId") == form_id
            or details.get("receiptId") == receipt_id
        ):
            events.append(payload)
    events.sort(key=_event_timestamp)
    timeline = []
    for idx, event in enumerate(events):
        timeline.append(
            {
                "action": event.get("action", "status_updated"),
                "timestamp": _event_timestamp(event),
                "isCurrent": idx == len(events) - 1,
            }
        )
    return timeline


def _fetch_archive_page(
    user_id: str, *, cursor: str | None = None, page_size: int = 200
) -> tuple[list[dict], str | None, int]:
    if firebase_config.db is None:
        return [], None, 0
    base_query = firebase_config.db.collection("combined_documents").where(
        "userId", "==", user_id
    )
    total_count = len(list(base_query.stream()))
    query = base_query.order_by("createdAt", direction=firestore.Query.DESCENDING).limit(
        page_size
    )
    if cursor:
        query = query.start_after({"createdAt": cursor})
    docs = list(query.stream())
    next_cursor = docs[-1].to_dict().get("createdAt") if len(docs) == page_size else None
    return [_extract_submission(doc) for doc in docs], next_cursor, total_count


def _apply_filters(items: list[dict], args) -> list[dict]:
    date_from = _to_iso_date(args.get("date_from"))
    date_to = _to_iso_date(args.get("date_to"))
    month = args.get("month")
    year = args.get("year")
    merchant = (args.get("merchant") or "").strip().lower()
    category = (args.get("category") or "").strip()

    filtered = []
    for item in items:
        item_date = _to_iso_date(item.get("date"))
        if date_from and (not item_date or item_date < date_from):
            continue
        if date_to and (not item_date or item_date > date_to):
            continue
        if month and item_date:
            try:
                month_value = f"{int(month):02d}"
            except ValueError:
                month_value = ""
            if month_value and month_value != item_date[5:7]:
                continue
        if year and item_date:
            if str(year) != item_date[:4]:
                continue
        if merchant and merchant not in (item.get("merchant") or "").lower():
            continue
        if category and category in ALLOWED_CATEGORIES and item.get("category") != category:
            continue
        filtered.append(item)
    return filtered


def _apply_sort(items: list[dict], sort_key: str | None) -> list[dict]:
    key = sort_key or "newest"
    if key == "oldest":
        return sorted(items, key=lambda i: i.get("createdAt") or "")
    if key == "amount_high":
        return sorted(items, key=lambda i: _to_float(i.get("totalAmount")), reverse=True)
    if key == "amount_low":
        return sorted(items, key=lambda i: _to_float(i.get("totalAmount")))
    return sorted(items, key=lambda i: i.get("createdAt") or "", reverse=True)


@archive_bp.get("/status")
@require_auth
def archive_status():
    return jsonify({"module": "archive", "status": "ok"})


@archive_bp.get("/")
@require_auth
def employee_archive():
    uid = g.user.get("uid")
    page_size = min(max(int(request.args.get("limit", 200)), 1), 200)
    cursor = request.args.get("cursor")
    submissions, next_cursor, total = _fetch_archive_page(
        uid, cursor=cursor, page_size=page_size
    )
    submissions = [item for item in submissions if item.get("userId") == uid]
    submissions = _apply_sort(_apply_filters(submissions, request.args), request.args.get("sort"))
    return render_template(
        "archive/employee_archive.html",
        submissions=submissions,
        next_cursor=next_cursor,
        total_submissions=total,
        showing_count=len(submissions),
        filters={
            "date_from": request.args.get("date_from", ""),
            "date_to": request.args.get("date_to", ""),
            "month": request.args.get("month", ""),
            "year": request.args.get("year", ""),
            "merchant": request.args.get("merchant", ""),
            "category": request.args.get("category", ""),
            "sort": request.args.get("sort", "newest"),
        },
        allowed_categories=sorted(ALLOWED_CATEGORIES),
    )


@archive_bp.get("/data")
@require_auth
def employee_archive_data():
    uid = g.user.get("uid")
    page_size = min(max(int(request.args.get("limit", 200)), 1), 200)
    cursor = request.args.get("cursor")
    submissions, next_cursor, total = _fetch_archive_page(
        uid, cursor=cursor, page_size=page_size
    )
    submissions = [item for item in submissions if item.get("userId") == uid]
    submissions = _apply_sort(_apply_filters(submissions, request.args), request.args.get("sort"))
    return jsonify(
        {
            "success": True,
            "results": submissions,
            "nextCursor": next_cursor,
            "total": total,
            "showing": len(submissions),
        }
    )


@archive_bp.get("/<document_id>")
@require_auth
def archive_document_detail(document_id: str):
    document = combined_document_repository.get_document(document_id)
    if document is None:
        return jsonify({"success": False, "error": "Document not found"}), 404

    is_admin = g.user.get("role") == "admin"
    if document.userId != g.user.get("uid") and not is_admin:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    receipt_model = receipt_repository.get_receipt(document.receiptId)
    form_model = form_repository.get_form(document.formId)
    if receipt_model is None or form_model is None:
        return jsonify({"success": False, "error": "Linked records not found"}), 404

    receipt = asdict(receipt_model)
    form = asdict(form_model)
    timeline = _get_audit_timeline(
        document_id=document.id,
        form_id=document.formId,
        receipt_id=document.receiptId,
        user_id=document.userId,
    )
    can_delete = (
        form.get("status") == "draft"
        or receipt.get("processingStatus") in {"uploaded", "draft"}
    )
    return render_template(
        "archive/document_detail.html",
        document=document,
        receipt=receipt,
        form=form,
        timeline=timeline,
        can_delete=can_delete,
    )
