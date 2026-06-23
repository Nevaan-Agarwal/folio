from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from io import BytesIO

from flask import Blueprint, g, jsonify, render_template, request, send_file, url_for

from config import database as database_config
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
    "Training/Workshop",
    "Other",
}


def _to_iso_date(value: str | None) -> str | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
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
    if database_config.db is None:
        return {}
    payload = doc.to_dict() or {}
    receipt_id = payload.get("receiptId")
    form_id = payload.get("formId")
    receipt = {}
    form = {}
    if receipt_id:
        receipt_doc = database_config.db.collection("receipts").document(receipt_id).get()
        if receipt_doc.exists:
            receipt = receipt_doc.to_dict() or {}
    if form_id:
        form_doc = database_config.db.collection("forms").document(form_id).get()
        if form_doc.exists:
            form = form_doc.to_dict() or {}
    date_value = (
        form.get("date")
        or form.get("dateOfHospitality")
        or _to_iso_date(payload.get("createdAt"))
    )
    document_id = payload.get("id", doc.id)
    return {
        "id": doc.id,
        "documentId": document_id,
        "receiptId": receipt_id or "",
        "formId": form_id or "",
        "merchant": form.get("merchant") or receipt.get("merchant") or "Unknown Merchant",
        "date": _to_iso_date(date_value) or "",
        "category": form.get("expenseCategory") or "Other",
        "occasion": form.get("occasion") or "",
        "totalAmount": _to_float(form.get("totalAmount") or receipt.get("total")),
        "status": receipt.get("processingStatus", "processing"),
        "thumbnailUrl": (
            url_for("receipts.receipt_image", receipt_id=receipt_id) if receipt_id else ""
        ),
        "pdfUrl": url_for("archive.archive_document_pdf", document_id=document_id),
        "createdAt": payload.get("createdAt", ""),
        "userId": payload.get("userId", ""),
    }


def _event_timestamp(event: dict) -> str:
    ts = event.get("timestamp")
    if not ts:
        return ""
    return str(ts)


def _get_audit_timeline(document_id: str, form_id: str, receipt_id: str, user_id: str) -> list[dict]:
    if database_config.db is None:
        return []
    events = []
    for doc in (
        database_config.db.collection("auditLogs")
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
    if database_config.db is None:
        return [], None, 0
    base_query = database_config.db.collection("combined_documents").where(
        "userId", "==", user_id
    )
    total_count = len(list(base_query.stream()))
    query = base_query.order_by("createdAt", direction="DESCENDING").limit(
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
    form["invoiceAmount"] = _to_float(form.get("invoiceAmount"))
    form["tip"] = _to_float(form.get("tip"))
    form["totalAmount"] = _to_float(form.get("totalAmount"))
    timeline = _get_audit_timeline(
        document_id=document.id,
        form_id=document.formId,
        receipt_id=document.receiptId,
        user_id=document.userId,
    )
    can_delete = document.userId == g.user.get("uid") or is_admin
    return render_template(
        "archive/document_detail.html",
        document=document,
        documentPdfUrl=url_for("archive.archive_document_pdf", document_id=document.id),
        receipt=receipt,
        form=form,
        timeline=timeline,
        can_delete=can_delete,
    )


@archive_bp.get("/<document_id>/pdf")
@require_auth
def archive_document_pdf(document_id: str):
    document = combined_document_repository.get_document(document_id)
    if document is None:
        return jsonify({"success": False, "error": "Document not found"}), 404

    is_admin = g.user.get("role") == "admin"
    if document.userId != g.user.get("uid") and not is_admin:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    if database_config.bucket is None or not document.filePath:
        return jsonify({"success": False, "error": "PDF file unavailable"}), 404

    try:
        blob = database_config.bucket.blob(document.filePath)
        pdf_bytes = blob.download_as_bytes()
    except Exception:
        return jsonify({"success": False, "error": "PDF file unavailable"}), 404

    filename = f"{document.id}.pdf"
    if "/" in document.filePath:
        filename = document.filePath.rsplit("/", 1)[-1] or filename
    as_attachment = request.args.get("download") in {"1", "true", "yes"}
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=as_attachment,
        download_name=filename,
    )
