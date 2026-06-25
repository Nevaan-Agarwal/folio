from __future__ import annotations

import threading
import logging
from dataclasses import asdict
from datetime import datetime, timezone

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, session, url_for

from middleware.auth_middleware import require_auth
from middleware.rate_limiter import RATE_LIMITS, limiter, user_rate_limit_key
from repositories import (
    audit_repository,
    combined_document_repository,
    form_repository,
    receipt_repository,
)
from services import pdf_service
forms_bp = Blueprint("forms", __name__)
logger = logging.getLogger(__name__)

REQUIRED_FORM_FIELDS = {
    "dateOfHospitality",
    "locationOfHospitality",
    "host",
    "occasion",
    "invoiceAmount",
    "totalAmount",
    "merchant",
}


@forms_bp.get("/status")
@require_auth
def forms_status():
    return jsonify({"module": "forms", "status": "ok"})


def _to_json_safe_form(form):
    payload = asdict(form)
    if form.createdAt:
        payload["createdAt"] = form.createdAt.isoformat()
    if form.updatedAt:
        payload["updatedAt"] = form.updatedAt.isoformat()
    return payload


def _ensure_form_access(form, *, strict_owner: bool = False) -> bool:
    user_id = g.user.get("uid")
    is_admin = g.user.get("role") == "admin"
    if not form:
        return False
    if form.userId:
        if strict_owner:
            return form.userId == user_id
        if form.userId == user_id or is_admin:
            return True
    # Backward compatibility for forms created before userId was persisted.
    receipt = receipt_repository.get_receipt(form.receiptId)
    if receipt is None:
        return False
    if strict_owner:
        return receipt.userId == user_id
    return receipt.userId == user_id or is_admin


def _sanitize_form_input(payload: dict) -> dict:
    return {
        "type": payload.get("type"),
        "expenseCategory": payload.get("expenseCategory"),
        "host": payload.get("host"),
        "hostedPersons": [
            line.strip() for line in (payload.get("hostedPersons", "") or "").splitlines() if line.strip()
        ],
        "occasion": payload.get("occasion"),
        "dateOfHospitality": payload.get("dateOfHospitality"),
        "locationOfHospitality": payload.get("locationOfHospitality"),
        "invoiceAmount": payload.get("invoiceAmount"),
        "tip": payload.get("tip"),
        "totalAmount": payload.get("totalAmount"),
        "merchant": payload.get("merchant"),
        "receiptNumber": payload.get("receiptNumber"),
        "date": payload.get("date"),
        "place": payload.get("place") or payload.get("address"),
    }


def _form_to_template_payload(form, document=None):
    hosted_persons_text = "\n".join(form.hostedPersons or [])
    ai_confidence = form.aiConfidence or {}
    missing_fields = set(form.missingFields or [])
    completed = 0
    for field in REQUIRED_FORM_FIELDS:
        value = getattr(form, field, None)
        if isinstance(value, list):
            if value:
                completed += 1
        elif value not in (None, "", []):
            completed += 1
    document_id = document.id if document else f"{form.id}-{form.receiptId}"
    return {
        "id": form.id,
        "receiptId": form.receiptId,
        "userId": form.userId,
        "type": form.type,
        "expenseCategory": form.expenseCategory,
        "host": form.host or session.get("name", ""),
        "hostedPersonsText": hosted_persons_text,
        "occasion": form.occasion,
        "dateOfHospitality": form.dateOfHospitality,
        "locationOfHospitality": form.locationOfHospitality,
        "invoiceAmount": form.invoiceAmount if form.invoiceAmount is not None else "",
        "tip": form.tip if form.tip is not None else "",
        "totalAmount": form.totalAmount if form.totalAmount is not None else "",
        "merchant": form.merchant,
        "receiptNumber": form.receiptNumber,
        "date": form.date,
        "place": form.place,
        "address": form.place,
        "missingFields": list(missing_fields),
        "aiConfidence": ai_confidence,
        "needsManualReview": form.needsManualReview,
        "status": form.status,
        "isReadOnly": form.status == "approved",
        "documentId": document_id,
        "documentPdfUrl": url_for("archive.archive_document_pdf", document_id=document_id),
        "emailDeliveryStatus": (document.emailDeliveryStatus if document else "pending"),
        "emailError": (document.emailError if document else ""),
        "emailSent": bool(document.emailSent) if document else False,
        "documentEmail": document.userEmail if document else "",
        "completedCount": completed,
        "attentionCount": len(missing_fields),
    }


def _generate_pdf_safe(form_id: str, receipt_id: str, user_id: str) -> None:
    try:
        pdf_service.generate_pdf(form_id, receipt_id, user_id)
    except Exception as exc:
        logger.exception("PDF generation failed for form %s receipt %s", form_id, receipt_id)
        receipt_repository.update_receipt(
            receipt_id,
            {"processingStatus": "error", "errorMessage": f"PDF generation failed: {exc}"},
        )


@forms_bp.get("/receipt/<receipt_id>/review")
@require_auth
def review_form_by_receipt(receipt_id: str):
    form = form_repository.get_form_by_receipt(receipt_id)
    if form is None:
        return jsonify({"status": "error", "message": "Form not found."}), 404
    if not _ensure_form_access(form):
        return jsonify({"status": "error", "message": "Forbidden"}), 403

    receipt = receipt_repository.get_receipt(receipt_id)
    if receipt is None:
        return jsonify({"status": "error", "message": "Receipt not found."}), 404
    combined_doc = combined_document_repository.get_document(f"{form.id}-{receipt_id}")
    view_data = _form_to_template_payload(form, combined_doc)
    view_data["pdfGenerated"] = receipt.processingStatus in {"pdf_generated", "completed"}
    return render_template(
        "forms/review.html",
        form=view_data,
        receipt=receipt,
    )


@forms_bp.get("/<form_id>/review")
@require_auth
def review_form(form_id: str):
    form = form_repository.get_form(form_id)
    if form is None:
        return jsonify({"status": "error", "message": "Form not found."}), 404
    if not _ensure_form_access(form):
        return jsonify({"status": "error", "message": "Forbidden"}), 403
    return redirect(url_for("forms.review_form_by_receipt", receipt_id=form.receiptId))


@forms_bp.get("/<form_id>")
@require_auth
def get_form_json(form_id: str):
    form = form_repository.get_form(form_id)
    if form is None:
        return jsonify({"status": "error", "message": "Form not found."}), 404
    if not _ensure_form_access(form):
        return jsonify({"status": "error", "message": "Forbidden"}), 403
    return jsonify({"success": True, "form": _to_json_safe_form(form)})


@forms_bp.post("/<form_id>/save-draft")
@require_auth
def save_draft(form_id: str):
    form = form_repository.get_form(form_id)
    if form is None:
        return jsonify({"status": "error", "message": "Form not found."}), 404
    if not _ensure_form_access(form, strict_owner=True):
        return jsonify({"status": "error", "message": "Forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    updates = _sanitize_form_input(payload)
    updates["status"] = "draft"
    sanitized_updates = {k: v for k, v in updates.items() if v is not None}

    form_repository.update_form(form_id, sanitized_updates)
    saved_at = datetime.now(timezone.utc).isoformat()
    return jsonify({"success": True, "savedAt": saved_at})


@forms_bp.post("/<form_id>/approve")
@require_auth
@limiter.limit(RATE_LIMITS["/forms/*/approve"], key_func=user_rate_limit_key)
def approve_form(form_id: str):
    form = form_repository.get_form(form_id)
    if form is None:
        return jsonify({"status": "error", "message": "Form not found."}), 404
    if not _ensure_form_access(form, strict_owner=True):
        return jsonify({"status": "error", "message": "Forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    submitted_data = _sanitize_form_input(payload)
    merged = {**_to_json_safe_form(form), **{k: v for k, v in submitted_data.items() if v is not None}}

    approved_payload = {k: v for k, v in submitted_data.items() if v is not None}
    approved_payload["status"] = "approved"
    form_repository.approve_form(form_id, approved_payload)
    audit_repository.create_log(
        user_id=g.user.get("uid"),
        action="form_approved",
        details={
            "formId": form_id,
            "receiptId": form.receiptId,
            "total": merged.get("totalAmount"),
            "expenseCategory": merged.get("expenseCategory"),
        },
        request=request,
    )
    worker = threading.Thread(
        target=_generate_pdf_safe,
        args=(form_id, form.receiptId, g.user.get("uid")),
        daemon=True,
    )
    worker.start()
    return jsonify(
        {
            "success": True,
            "message": "Form approved. Generating PDF...",
            "redirectUrl": f"/receipts/{form.receiptId}/processing",
        }
    )


@forms_bp.post("/<form_id>/reject")
@require_auth
def reject_form(form_id: str):
    form = form_repository.get_form(form_id)
    if form is None:
        return jsonify({"status": "error", "message": "Form not found."}), 404
    if not _ensure_form_access(form, strict_owner=True):
        return jsonify({"status": "error", "message": "Forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or "").strip()
    if not reason:
        return jsonify({"status": "error", "message": "Rejection reason is required."}), 400

    form_repository.reject_form(form_id, reason)
    audit_repository.create_log(
        user_id=g.user.get("uid"),
        action="form_rejected",
        details={"formId": form_id, "reason": reason},
        request=request,
    )
    flash("Form rejected. Please upload a clearer receipt.", "error")
    return jsonify(
        {
            "success": True,
            "message": "Form rejected. Please upload a clearer receipt.",
            "redirectUrl": url_for("receipts.new_receipt"),
        }
    )
