from __future__ import annotations

from flask import Blueprint, g, jsonify

from middleware.auth_middleware import require_auth
from repositories import (
    audit_repository,
    combined_document_repository,
    form_repository,
    receipt_repository,
)
from config import database as database_config

documents_bp = Blueprint("documents", __name__)


@documents_bp.post("/documents/<doc_id>/resend-email")
@require_auth
def resend_email(doc_id: str):
    return (
        jsonify(
            {
                "success": False,
                "error": "Email sending is disabled for this deployment.",
            }
        ),
        410,
    )


@documents_bp.post("/documents/<doc_id>/delete")
@require_auth
def delete_submission(doc_id: str):
    document = combined_document_repository.get_document(doc_id)
    if document is None:
        return jsonify({"success": False, "error": "Document not found"}), 404

    is_admin = g.user.get("role") == "admin"
    if document.userId != g.user.get("uid") and not is_admin:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    try:
        # Delete the combined record first to satisfy FK constraints in SQL backends.
        combined_document_repository.delete_document(doc_id)
        if document.formId:
            form_repository.delete_form(document.formId)
        if document.receiptId:
            receipt_repository.delete_receipt(document.receiptId)
    except Exception as exc:
        return jsonify({"success": False, "error": f"Delete failed: {exc}"}), 500

    if database_config.bucket is not None and document.filePath:
        try:
            database_config.bucket.blob(document.filePath).delete()
        except Exception:
            # File cleanup should not block deletion of metadata.
            pass

    audit_repository.create_log(
        user_id=g.user.get("uid"),
        action="document_deleted",
        details={"documentId": doc_id},
    )
    return jsonify({"success": True})
