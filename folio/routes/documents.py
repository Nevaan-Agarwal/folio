from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, g, jsonify

from middleware.auth_middleware import require_auth
from repositories import (
    audit_repository,
    combined_document_repository,
    form_repository,
    receipt_repository,
    user_repository,
)
from services import email_service
from config import database as database_config

documents_bp = Blueprint("documents", __name__)


@documents_bp.post("/documents/<doc_id>/resend-email")
@require_auth
def resend_email(doc_id: str):
    document = combined_document_repository.get_document(doc_id)
    if document is None:
        return jsonify({"success": False, "error": "Document not found"}), 404
    if document.userId != g.user.get("uid"):
        return jsonify({"success": False, "error": "Forbidden"}), 403
    if not document.emailSent:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Email has never been sent for this document yet.",
                }
            ),
            400,
        )

    blob = database_config.bucket.blob(document.filePath)
    pdf_bytes = blob.download_as_bytes()

    form = form_repository.get_form(document.formId)
    user = user_repository.get_user(document.userId)
    form_data = asdict(form) if form else {}
    if user:
        form_data["language"] = user.language
    result = email_service.send_pdf_delivery(
        to_email=document.userEmail or (user.email if user else ""),
        user_name=(f"{user.firstName} {user.surname}".strip() if user else g.user.get("name", "User")),
        form_data=form_data,
        pdf_download_url=document.downloadUrl,
        pdf_bytes=pdf_bytes,
        document_id=doc_id,
    )
    if result.get("success"):
        audit_repository.create_log(
            user_id=g.user.get("uid"),
            action="email_resent",
            details={"documentId": doc_id, "email": document.userEmail or (user.email if user else "")},
        )
    status_code = 200 if result.get("success") else 500
    return jsonify(result), status_code


@documents_bp.post("/documents/<doc_id>/delete")
@require_auth
def delete_submission(doc_id: str):
    document = combined_document_repository.get_document(doc_id)
    if document is None:
        return jsonify({"success": False, "error": "Document not found"}), 404

    is_admin = g.user.get("role") == "admin"
    if document.userId != g.user.get("uid") and not is_admin:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    if database_config.bucket is not None and document.filePath:
        try:
            database_config.bucket.blob(document.filePath).delete()
        except Exception:
            # File cleanup should not block deletion of metadata.
            pass

    if document.formId:
        form_repository.delete_form(document.formId)
    if document.receiptId:
        receipt_repository.delete_receipt(document.receiptId)
    combined_document_repository.delete_document(doc_id)

    audit_repository.create_log(
        user_id=g.user.get("uid"),
        action="document_deleted",
        details={"documentId": doc_id},
    )
    return jsonify({"success": True})
