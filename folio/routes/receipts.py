from __future__ import annotations

import tempfile
from pathlib import Path
from threading import Thread
from urllib.parse import unquote, urlparse
from urllib.request import urlopen
from uuid import uuid4

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from middleware.auth_middleware import require_auth
from middleware.rate_limiter import RATE_LIMITS, limiter, user_rate_limit_key
from repositories import audit_repository, receipt_repository
from services.ai_service import AiService
from services.ocr_service import OcrService
from services.storage_service import (
    ALLOWED_EXTENSIONS,
    MAX_RECEIPT_SIZE_BYTES,
    StorageUploadException,
    upload_receipt_image,
)

receipts_bp = Blueprint("receipts", __name__)


STEP_BY_STATUS = {
    "uploaded": 1,
    "ocr_processing": 2,
    "ocr_complete": 3,
    "ai_processing": 4,
    "awaiting_review": 5,
    "pdf_generation": 6,
    "pdf_generated": 6,
    "completed": 6,
}

STATUS_MESSAGES = {
    "en": {
        "uploaded": "Receipt saved successfully.",
        "ocr_processing": "Enhancing image and scanning text.",
        "ocr_complete": "OCR complete. Preparing AI extraction.",
        "ai_processing": "AI processing receipt details.",
        "awaiting_review": "Ready to review extracted fields.",
        "pdf_generation": "Generating PDF and preparing email delivery.",
        "pdf_generated": "PDF generated.",
        "completed": "Pipeline completed.",
        "error": "Processing failed.",
    },
    "de": {
        "uploaded": "Beleg wurde gespeichert.",
        "ocr_processing": "Bild wird verbessert und Text wird gescannt.",
        "ocr_complete": "OCR abgeschlossen. KI-Analyse wird vorbereitet.",
        "ai_processing": "KI verarbeitet die Belegdaten.",
        "awaiting_review": "Bereit zur Pruefung der extrahierten Felder.",
        "pdf_generation": "PDF wird erstellt und E-Mail-Zustellung vorbereitet.",
        "pdf_generated": "PDF wurde erstellt.",
        "completed": "Verarbeitung abgeschlossen.",
        "error": "Verarbeitung fehlgeschlagen.",
    },
}


def _log_status_transition(
    receipt_id: str,
    user_id: str,
    status: str,
    detail: str = "",
    extra_details: dict | None = None,
) -> None:
    action = None
    if status == "ocr_complete":
        action = "ocr_completed"
    elif status == "awaiting_review":
        action = "ai_processed"
    if action:
        payload_details = {"receiptId": receipt_id, "status": status, "detail": detail}
        if extra_details:
            payload_details.update(extra_details)
        audit_repository.create_log(
            user_id=user_id,
            action=action,
            details=payload_details,
        )


def _download_image_to_temp(receipt_id: str, image_url: str) -> str:
    parsed = urlparse(image_url)
    extension = Path(parsed.path).suffix or ".png"
    temp_path = Path(tempfile.gettempdir()) / f"folio_{receipt_id}_pipeline{extension}"
    with urlopen(image_url, timeout=20) as response:
        temp_path.write_bytes(response.read())
    return str(temp_path)


def _resolve_local_image_path(image_url: str) -> Path | None:
    parsed = urlparse(image_url or "")
    if parsed.scheme == "file":
        decoded = unquote(parsed.path or "")
        if decoded.startswith("/") and len(decoded) > 2 and decoded[2] == ":":
            decoded = decoded[1:]
        candidate = Path(decoded)
        return candidate if candidate.exists() else None
    if parsed.scheme:
        return None
    candidate = Path(image_url or "")
    return candidate if candidate.exists() else None


def process_receipt_pipeline(receipt_id: str, image_url: str, user_id: str):
    """
    Runs the complete pipeline in a background thread:
    1. Download image from local storage to temp file
    2. Run OCR (updates status to ocr_processing -> ocr_complete)
    3. Run AI processing (updates status to ai_processing -> awaiting_review)
    4. On completion: update status to awaiting_review
    5. On any step failure: update status to error with error details
    """
    temp_image_path = ""
    try:
        temp_image_path = _download_image_to_temp(receipt_id=receipt_id, image_url=image_url)

        receipt_repository.update_processing_status(receipt_id, "ocr_processing")
        _log_status_transition(receipt_id, user_id, "ocr_processing")

        ocr_service = OcrService()
        ocr_result = ocr_service.run_ocr(receipt_id=receipt_id, image_path=temp_image_path)
        if ocr_result.get("error"):
            raise RuntimeError(ocr_result["error"])
        _log_status_transition(
            receipt_id,
            user_id,
            "ocr_complete",
            extra_details={"confidence": ocr_result.get("confidence", 0)},
        )

        receipt_repository.update_processing_status(receipt_id, "ai_processing")
        _log_status_transition(receipt_id, user_id, "ai_processing")

        ai_service = AiService()
        ai_result = ai_service.process_receipt(
            receipt_id=receipt_id,
            ocr_text=ocr_result.get("raw_text", ""),
        )
        if ai_result.get("error"):
            raise RuntimeError(ai_result["error"])

        receipt_repository.update_processing_status(receipt_id, "awaiting_review")
        missing_fields = list(ai_result.get("missingFields") or [])
        needs_manual_review = bool(missing_fields)
        _log_status_transition(
            receipt_id,
            user_id,
            "awaiting_review",
            extra_details={
                "missingFields": missing_fields,
                "needsManualReview": needs_manual_review,
            },
        )
        if needs_manual_review:
            audit_repository.create_log(
                user_id=user_id,
                action="manual_review_required",
                details={"receiptId": receipt_id, "missingFields": missing_fields},
            )
    except Exception as exc:
        receipt_repository.update_receipt(
            receipt_id,
            {"processingStatus": "error", "errorMessage": str(exc)},
        )
        _log_status_transition(receipt_id, user_id, "error", detail=str(exc))
        audit_repository.create_log(
            user_id=user_id,
            action="ocr_failed",
            details={"receiptId": receipt_id, "error": str(exc)},
        )
    finally:
        try:
            Path(temp_image_path).unlink(missing_ok=True)
        except OSError:
            pass


@receipts_bp.get("/status")
@require_auth
def receipts_status():
    user_id = session.get("uid")
    receipts = receipt_repository.get_user_receipts(user_id) if user_id else []
    return jsonify({"module": "receipts", "status": "ok", "count": len(receipts)})


@receipts_bp.get("/new")
@require_auth
def new_receipt():
    return render_template("receipts/upload.html")


@receipts_bp.get("/<receipt_id>/processing")
@require_auth
def processing(receipt_id: str):
    receipt = receipt_repository.get_receipt(receipt_id)
    if receipt is None:
        return jsonify({"status": "error", "message": "Receipt not found."}), 404
    user_id = session.get("uid")
    is_admin = session.get("role") == "admin"
    if receipt.userId != user_id and not is_admin:
        return jsonify({"status": "error", "message": "Forbidden"}), 403
    return render_template("receipts/processing.html", receipt=receipt)


@receipts_bp.get("/<receipt_id>/review")
@require_auth
def review_redirect(receipt_id: str):
    return redirect(url_for("forms.review_form_by_receipt", receipt_id=receipt_id))


@receipts_bp.get("/<receipt_id>/image")
@require_auth
def receipt_image(receipt_id: str):
    receipt = receipt_repository.get_receipt(receipt_id)
    if receipt is None:
        return jsonify({"status": "error", "message": "Receipt not found."}), 404

    user_id = session.get("uid")
    is_admin = session.get("role") == "admin"
    if receipt.userId != user_id and not is_admin:
        return jsonify({"status": "error", "message": "Forbidden"}), 403

    local_path = _resolve_local_image_path(receipt.imageUrl)
    if local_path is not None:
        return send_file(local_path)
    return redirect(receipt.imageUrl)


@receipts_bp.get("/<receipt_id>/process")
@require_auth
def process_receipt(receipt_id: str):
    receipt = receipt_repository.get_receipt(receipt_id)
    if receipt is None:
        return jsonify({"status": "error", "message": "Receipt not found."}), 404
    user_id = session.get("uid")
    is_admin = session.get("role") == "admin"
    if receipt.userId != user_id and not is_admin:
        return jsonify({"status": "error", "message": "Forbidden"}), 403

    if receipt.processingStatus in {"ocr_processing", "ai_processing", "pdf_generation"}:
        return jsonify({"status": receipt.processingStatus}), 200

    # Completed/ready states should never restart OCR+AI.
    if receipt.processingStatus in {"awaiting_review", "pdf_generated", "completed"}:
        return jsonify({"status": receipt.processingStatus}), 200

    Thread(
        target=process_receipt_pipeline,
        args=(receipt_id, receipt.imageUrl, receipt.userId),
        daemon=True,
    ).start()
    return jsonify({"status": "processing"}), 200


@receipts_bp.get("/<receipt_id>/status")
@require_auth
def receipt_status(receipt_id: str):
    receipt = receipt_repository.get_receipt(receipt_id)
    if receipt is None:
        return jsonify({"status": "error", "message": "Receipt not found."}), 404
    user_id = session.get("uid")
    is_admin = session.get("role") == "admin"
    if receipt.userId != user_id and not is_admin:
        return jsonify({"status": "error", "message": "Forbidden"}), 403

    status = receipt.processingStatus
    lang = session.get("lang", "en")
    messages = STATUS_MESSAGES.get(lang, STATUS_MESSAGES["en"])
    step = STEP_BY_STATUS.get(status, 1)
    message = messages.get(status, messages.get("error", "Processing update unavailable."))

    payload = {
        "status": status,
        "reviewStatus": receipt.reviewStatus,
        "confidence": receipt.ocrConfidence or 0,
        "step": step,
        "message": message,
    }
    if status in {"awaiting_review", "pdf_generated", "completed"}:
        payload["redirectUrl"] = f"/receipts/{receipt_id}/review"
    if status == "error":
        payload["error"] = getattr(receipt, "errorMessage", None) or "Processing failed."
    return jsonify(payload)


@receipts_bp.post("/upload")
@require_auth
@limiter.limit(RATE_LIMITS["/receipts/upload"], key_func=user_rate_limit_key)
def upload_receipt():
    upload = request.files.get("receipt")

    if not upload or not upload.filename:
        return jsonify({"status": "error", "message": "Please select a receipt file."}), 400

    extension = upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
    if extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"Invalid file type. Allowed formats: {allowed}.",
                }
            ),
            400,
        )

    upload.stream.seek(0, 2)
    file_size = upload.stream.tell()
    upload.stream.seek(0)
    if file_size > MAX_RECEIPT_SIZE_BYTES:
        return (
            jsonify({"status": "error", "message": "File is too large. Maximum size is 15MB."}),
            400,
        )

    user_id = session.get("uid")
    if not user_id:
        return jsonify({"status": "error", "message": "Authentication required."}), 401

    receipt_id = str(uuid4())

    try:
        image_url = upload_receipt_image(upload, user_id=user_id, receipt_id=receipt_id)
        saved_receipt_id = receipt_repository.create_receipt(
            user_id=user_id,
            image_url=image_url,
            receipt_id=receipt_id,
        )
    except StorageUploadException as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception:
        return jsonify({"status": "error", "message": "Could not save receipt."}), 500

    audit_repository.create_log(
        user_id=user_id,
        action="receipt_uploaded",
        details={"receiptId": saved_receipt_id, "fileSize": int(file_size)},
        request=request,
    )
    _log_status_transition(saved_receipt_id, user_id, "uploaded")

    if not current_app.testing:
        thread = Thread(
            target=process_receipt_pipeline,
            args=(saved_receipt_id, image_url, user_id),
            daemon=True,
        )
        thread.start()

    return (
        jsonify(
            {
                "receiptId": saved_receipt_id,
                "imageUrl": image_url,
                "status": "uploaded",
            }
        ),
        200,
    )
