from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, g, render_template, session, url_for

from config import database as database_config
from middleware.auth_middleware import require_auth
from utils.helpers import get_current_language

dashboard_bp = Blueprint("dashboard", __name__)


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _to_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_date_text(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if value:
        return str(value)[:10]
    return ""


def _current_month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _get_greeting(name: str) -> str:
    hour = datetime.now().hour
    lang = get_current_language()
    if lang == "de":
        if hour < 12:
            part = "Guten Morgen"
        elif hour < 18:
            part = "Guten Tag"
        else:
            part = "Guten Abend"
    else:
        if hour < 12:
            part = "Good morning"
        elif hour < 18:
            part = "Good afternoon"
        else:
            part = "Good evening"
    return f"{part}, {name or 'there'}"


def _all_docs(collection_name: str) -> list[tuple[str, dict]]:
    if database_config.db is None:
        return []
    docs = database_config.db.collection(collection_name).stream()
    results: list[tuple[str, dict]] = []
    for doc in docs:
        results.append((doc.id, doc.to_dict() or {}))
    return results


def _build_user_dashboard(user_id: str | None) -> dict:
    now = datetime.now(timezone.utc)
    month_start = _current_month_start(now)

    receipt_rows = _all_docs("receipts")
    user_receipts = {}
    for receipt_id, payload in receipt_rows:
        if user_id is not None and payload.get("userId") != user_id:
            continue
        user_receipts[receipt_id] = payload

    form_rows = _all_docs("forms")
    user_forms = []
    for _form_id, payload in form_rows:
        if user_id is not None and payload.get("userId") != user_id:
            continue
        user_forms.append(payload)

    doc_rows = _all_docs("combined_documents")
    user_docs = []
    for doc_id, payload in doc_rows:
        if user_id is not None and payload.get("userId") != user_id:
            continue
        user_docs.append((doc_id, payload))
    user_docs.sort(key=lambda item: _to_datetime(item[1].get("createdAt")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    recent_submissions = []
    for _doc_id, payload in user_docs[:5]:
        receipt_id = payload.get("receiptId", "")
        receipt_payload = user_receipts.get(receipt_id, {})
        total_amount = _to_float(payload.get("totalAmount"))
        if total_amount <= 0:
            total_amount = _to_float(receipt_payload.get("total"))
        recent_submissions.append(
            {
                "documentId": payload.get("id") or _doc_id,
                "merchant": payload.get("merchant") or receipt_payload.get("merchant") or "-",
                "date": _to_date_text(payload.get("createdAt")),
                "category": payload.get("category") or "Other",
                "amount": total_amount,
                "status": receipt_payload.get("processingStatus") or payload.get("status") or "processing",
                "thumbUrl": (
                    url_for("receipts.receipt_image", receipt_id=receipt_id)
                    if receipt_id
                    else ""
                ),
                "viewUrl": url_for("archive.archive_document_detail", document_id=payload.get("id") or _doc_id),
            }
        )

    action_required = []
    for payload in user_forms:
        receipt_id = payload.get("receiptId", "")
        status = (user_receipts.get(receipt_id, {}).get("processingStatus") or "").lower()
        if status == "awaiting_review":
            action_required.append(
                {
                    "receiptId": receipt_id,
                    "merchant": payload.get("merchant") or "-",
                    "link": url_for("forms.review_form_by_receipt", receipt_id=receipt_id),
                }
            )

    pending_review_count = len(action_required)

    submissions_this_month = 0
    total_spent_this_month = 0.0
    for _doc_id, payload in user_docs:
        created_at = _to_datetime(payload.get("createdAt", ""))
        if not created_at or created_at < month_start:
            continue
        submissions_this_month += 1
        total_spent_this_month += _to_float(payload.get("totalAmount"))

    return {
        "recentSubmissions": recent_submissions,
        "pendingReviewCount": pending_review_count,
        "completedThisMonth": submissions_this_month,
        "totalSpentThisMonth": round(total_spent_this_month, 2),
        "actionRequiredForms": action_required,
    }


def _build_admin_overview() -> dict:
    now = datetime.now(timezone.utc)
    month_start = _current_month_start(now)

    users = _all_docs("users")
    docs = _all_docs("combined_documents")
    receipts = _all_docs("receipts")

    company_spend = 0.0
    for _doc_id, payload in docs:
        created_at = _to_datetime(payload.get("createdAt", ""))
        if created_at and created_at >= month_start:
            company_spend += _to_float(payload.get("totalAmount"))

    pending_reviews = 0
    for _receipt_id, payload in receipts:
        if (payload.get("processingStatus") or "").lower() == "awaiting_review":
            pending_reviews += 1

    return {
        "allUsersTotal": len(users),
        "companySpendThisMonth": round(company_spend, 2),
        "pendingReviewsCompany": pending_reviews,
    }


@dashboard_bp.get("/dashboard")
@require_auth
def home():
    user_id = g.user.get("uid")
    role = g.user.get("role", "employee")
    first_name = (g.user.get("name") or "there").strip()

    user_data = _build_user_dashboard(user_id=None if role == "admin" else user_id)
    admin_data = _build_admin_overview() if role == "admin" else {}

    should_show_onboarding = not bool(session.get("onboarding_completed", False))

    return render_template(
        "dashboard/home.html",
        greeting=_get_greeting(first_name),
        firstName=first_name,
        pendingReviewCount=user_data["pendingReviewCount"],
        actionRequiredForms=user_data["actionRequiredForms"],
        recentSubmissions=user_data["recentSubmissions"],
        completedThisMonth=user_data["completedThisMonth"],
        totalSpentThisMonth=user_data["totalSpentThisMonth"],
        isAdmin=role == "admin",
        adminOverview=admin_data,
        showOnboarding=should_show_onboarding,
    )
