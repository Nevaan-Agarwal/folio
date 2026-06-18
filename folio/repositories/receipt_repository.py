"""Receipt repository for Firestore operations."""

from __future__ import annotations

from datetime import datetime, timezone

from config import firebase as firebase_config
from models.receipt import ReceiptModel


def _to_receipt_model(receipt_id: str, data: dict | None) -> ReceiptModel | None:
    if not data:
        return None
    uploaded_at = data.get("uploadedAt")
    if isinstance(uploaded_at, str):
        try:
            uploaded_at = datetime.fromisoformat(uploaded_at)
        except ValueError:
            uploaded_at = None

    return ReceiptModel(
        id=receipt_id,
        userId=data.get("userId", ""),
        imageUrl=data.get("imageUrl", ""),
        uploadedAt=uploaded_at,
        ocrText=data.get("ocrText", ""),
        ocrConfidence=data.get("ocrConfidence"),
        merchant=data.get("merchant", ""),
        address=data.get("address", ""),
        date=data.get("date", ""),
        currency=data.get("currency", ""),
        subtotal=data.get("subtotal"),
        tax=data.get("tax"),
        tip=data.get("tip"),
        total=data.get("total"),
        receiptNumber=data.get("receiptNumber", ""),
        processingStatus=data.get("processingStatus", "uploaded"),
        reviewStatus=data.get("reviewStatus", "draft"),
        errorMessage=data.get("errorMessage", ""),
    )


def create_receipt(user_id: str, image_url: str, receipt_id: str | None = None) -> str:
    payload = {
        "userId": user_id,
        "imageUrl": image_url,
        "uploadedAt": datetime.now(timezone.utc).isoformat(),
        "ocrText": "",
        "ocrConfidence": None,
        "merchant": "",
        "address": "",
        "date": "",
        "currency": "",
        "subtotal": None,
        "tax": None,
        "tip": None,
        "total": None,
        "receiptNumber": "",
        "processingStatus": "uploaded",
        "reviewStatus": "draft",
        "errorMessage": "",
    }

    collection = firebase_config.db.collection("receipts")
    if receipt_id:
        collection.document(receipt_id).set(payload)
        return receipt_id

    doc_ref = collection.document()
    doc_ref.set(payload)
    return doc_ref.id


def update_receipt(receipt_id: str, data: dict) -> None:
    firebase_config.db.collection("receipts").document(receipt_id).set(data, merge=True)


def get_receipt(receipt_id: str) -> ReceiptModel | None:
    doc = firebase_config.db.collection("receipts").document(receipt_id).get()
    if not doc.exists:
        return None
    return _to_receipt_model(receipt_id, doc.to_dict())


def get_user_receipts(user_id: str) -> list[ReceiptModel]:
    docs = (
        firebase_config.db.collection("receipts").where("userId", "==", user_id).stream()
    )
    receipts: list[ReceiptModel] = []
    for doc in docs:
        model = _to_receipt_model(doc.id, doc.to_dict())
        if model:
            receipts.append(model)
    return receipts


def get_all_receipts(requester_role: str = "employee") -> list[ReceiptModel]:
    if requester_role != "admin":
        raise PermissionError("Admin role required to list all receipts.")
    docs = firebase_config.db.collection("receipts").stream()
    receipts: list[ReceiptModel] = []
    for doc in docs:
        model = _to_receipt_model(doc.id, doc.to_dict())
        if model:
            receipts.append(model)
    return receipts


def update_processing_status(receipt_id: str, status: str) -> None:
    update_receipt(receipt_id, {"processingStatus": status})


def update_review_and_processing_status(
    receipt_id: str, review_status: str, processing_status: str
) -> None:
    update_receipt(
        receipt_id,
        {"reviewStatus": review_status, "processingStatus": processing_status},
    )
