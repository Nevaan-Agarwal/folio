"""Form repository for SQL-backed form records."""

from __future__ import annotations

from datetime import datetime, timezone

from config import database as database_config
from models.form import FormModel
from repositories import audit_repository, receipt_repository


def save_form(form_data: dict) -> str:
    user_id = str((form_data or {}).get("userId") or "system")
    try:
        ref = database_config.db.collection("forms").document()
        ref.set(form_data)
        audit_repository.create_log(
            user_id=user_id,
            action="db_transaction",
            details={
                "status": "success",
                "operation": "create",
                "collection": "forms",
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
                "collection": "forms",
                "docId": "",
                "error": str(exc),
            },
        )
        raise


def _parse_datetime(value):
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return value if isinstance(value, datetime) else None


def _to_form_model(form_id: str, data: dict | None) -> FormModel | None:
    if not data:
        return None
    hosted_persons = data.get("hostedPersons", [])
    if isinstance(hosted_persons, str):
        hosted_persons = [line.strip() for line in hosted_persons.splitlines() if line.strip()]
    if not isinstance(hosted_persons, list):
        hosted_persons = []

    return FormModel(
        id=form_id,
        receiptId=data.get("receiptId", ""),
        userId=data.get("userId", ""),
        type=data.get("type", "Hospitality Expense"),
        expenseCategory=data.get("expenseCategory", "Other"),
        host=data.get("host", ""),
        hostedPersons=hosted_persons,
        occasion=data.get("occasion", ""),
        dateOfHospitality=data.get("dateOfHospitality"),
        locationOfHospitality=data.get("locationOfHospitality", ""),
        invoiceAmount=data.get("invoiceAmount"),
        tip=data.get("tip"),
        totalAmount=data.get("totalAmount"),
        merchant=data.get("merchant", ""),
        receiptNumber=data.get("receiptNumber", ""),
        date=data.get("date"),
        place=data.get("place", ""),
        missingFields=data.get("missingFields", []),
        needsManualReview=bool(data.get("needsManualReview", False)),
        aiConfidence=data.get("aiConfidence", {}),
        status=data.get("status", "draft"),
        createdAt=_parse_datetime(data.get("createdAt")),
        updatedAt=_parse_datetime(data.get("updatedAt")),
    )


def create_form_from_ai_result(
    receipt_id: str,
    user_id: str | dict | None = None,
    ai_result: dict | None = None,
) -> str:
    # Backward compatibility: old call style create_form_from_ai_result(receipt_id, ai_result)
    if ai_result is None and isinstance(user_id, dict):
        ai_result = user_id
        receipt = receipt_repository.get_receipt(receipt_id)
        user_id = receipt.userId if receipt is not None else ""
    ai_result = ai_result or {}
    user_id = str(user_id or "")

    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "receiptId": receipt_id,
        "userId": user_id,
        "type": "Hospitality Expense",
        "expenseCategory": ai_result.get("expenseCategory"),
        "host": "",
        "hostedPersons": [],
        "occasion": ai_result.get("anlasDerBewirtung"),
        "dateOfHospitality": ai_result.get("tagDerBewirtung"),
        "locationOfHospitality": ai_result.get("ortDerBewirtung"),
        "invoiceAmount": ai_result.get("subtotal"),
        "tip": ai_result.get("tip"),
        "totalAmount": ai_result.get("total"),
        "merchant": ai_result.get("merchant"),
        "receiptNumber": ai_result.get("receiptNumber"),
        "date": ai_result.get("date"),
        "place": ai_result.get("ortDerBewirtung") or ai_result.get("address"),
        "missingFields": ai_result.get("missingFields", []),
        "needsManualReview": bool(ai_result.get("missingFields"))
        or float((ai_result.get("confidence") or {}).get("overall") or 0) < 0.7,
        "aiConfidence": ai_result.get("confidence", {}),
        "status": "draft",
        "createdAt": now_iso,
        "updatedAt": now_iso,
    }
    try:
        ref = database_config.db.collection("forms").document(receipt_id)
        ref.set(payload, merge=True)
        if not ref.get().exists:
            raise RuntimeError("Failed to persist generated form.")
        audit_repository.create_log(
            user_id=user_id or "system",
            action="db_transaction",
            details={
                "status": "success",
                "operation": "upsert",
                "collection": "forms",
                "docId": ref.id,
            },
        )
        return ref.id
    except Exception as exc:
        audit_repository.create_log(
            user_id=user_id or "system",
            action="db_transaction",
            details={
                "status": "failed",
                "operation": "upsert",
                "collection": "forms",
                "docId": str(receipt_id or ""),
                "error": str(exc),
            },
        )
        raise


def get_form(form_id: str) -> FormModel | None:
    doc = database_config.db.collection("forms").document(form_id).get()
    if not doc.exists:
        return None
    return _to_form_model(doc.id, doc.to_dict())


def get_form_by_receipt(receipt_id: str) -> FormModel | None:
    doc = database_config.db.collection("forms").document(receipt_id).get()
    if not doc.exists:
        return None
    return _to_form_model(doc.id, doc.to_dict())


def update_form(form_id: str, data: dict) -> None:
    payload = dict(data)
    payload["updatedAt"] = datetime.now(timezone.utc).isoformat()
    user_id = str(payload.get("userId") or "system")
    try:
        database_config.db.collection("forms").document(form_id).set(payload, merge=True)
        audit_repository.create_log(
            user_id=user_id,
            action="db_transaction",
            details={
                "status": "success",
                "operation": "update",
                "collection": "forms",
                "docId": form_id,
            },
        )
    except Exception as exc:
        audit_repository.create_log(
            user_id=user_id,
            action="db_transaction",
            details={
                "status": "failed",
                "operation": "update",
                "collection": "forms",
                "docId": form_id,
                "error": str(exc),
            },
        )
        raise


def approve_form(form_id: str, data: dict | None = None) -> None:
    form = get_form(form_id)
    if form is None:
        return
    payload = dict(data or {})
    payload["status"] = "approved"
    update_form(form_id, payload)
    receipt_repository.update_receipt(
        form.receiptId,
        {"reviewStatus": "approved", "processingStatus": "pdf_generation"},
    )


def reject_form(form_id: str, reason: str) -> None:
    form = get_form(form_id)
    if form is None:
        return
    update_form(form_id, {"status": "rejected", "rejectionReason": reason})
    receipt_repository.update_receipt(
        form.receiptId,
        {"reviewStatus": "rejected", "processingStatus": "uploaded"},
    )


def delete_form(form_id: str) -> None:
    if database_config.db is None:
        return
    try:
        database_config.db.collection("forms").document(form_id).delete()
        audit_repository.create_log(
            user_id="system",
            action="db_transaction",
            details={
                "status": "success",
                "operation": "delete",
                "collection": "forms",
                "docId": form_id,
            },
        )
    except Exception as exc:
        audit_repository.create_log(
            user_id="system",
            action="db_transaction",
            details={
                "status": "failed",
                "operation": "delete",
                "collection": "forms",
                "docId": form_id,
                "error": str(exc),
            },
        )
        raise
