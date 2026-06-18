from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import datetime, timezone
from io import BytesIO, StringIO
from urllib.parse import urlencode

from flask import Blueprint, Response, g, jsonify, render_template, request, url_for

from config import firebase as firebase_config
from repositories import audit_repository, form_repository, receipt_repository, user_repository
from services.analytics_service import analytics_service

from middleware.auth_middleware import require_admin
from middleware.rate_limiter import RATE_LIMITS, limiter, user_rate_limit_key

admin_bp = Blueprint("admin", __name__)

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


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _to_iso_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date().isoformat()
    except ValueError:
        return None


def _load_admin_archive_entries() -> list[dict]:
    if firebase_config.db is None:
        return []
    users_by_id = {}
    for user in user_repository.get_all_users("admin"):
        users_by_id[user.id] = user

    entries = []
    for doc in firebase_config.db.collection("combined_documents").stream():
        payload = doc.to_dict() or {}
        user_id = payload.get("userId", "")
        form = None
        receipt = None
        if payload.get("formId"):
            form = form_repository.get_form(payload["formId"])
        if payload.get("receiptId"):
            receipt = receipt_repository.get_receipt(payload["receiptId"])
        employee = users_by_id.get(user_id)
        form_data = asdict(form) if form else {}
        receipt_data = asdict(receipt) if receipt else {}
        merchant = payload.get("merchant") or form_data.get("merchant") or receipt_data.get("merchant") or "-"
        amount = _to_float(payload.get("totalAmount") or form_data.get("totalAmount") or receipt_data.get("total"))
        date_value = (
            form_data.get("date")
            or form_data.get("dateOfHospitality")
            or payload.get("createdAt", "")[:10]
        )
        entries.append(
            {
                "document_id": payload.get("id", doc.id),
                "user_id": user_id,
                "employee_name": (
                    f"{employee.firstName} {employee.surname}".strip() if employee else "-"
                ),
                "email": employee.email if employee else payload.get("userEmail", ""),
                "merchant": merchant,
                "date": _to_iso_date(date_value) or "",
                "category": payload.get("category") or form_data.get("expenseCategory") or "Other",
                "host": payload.get("host") or form_data.get("host") or "",
                "occasion": payload.get("occasion") or form_data.get("occasion") or "",
                "total_amount": amount,
                "currency": payload.get("currency") or receipt_data.get("currency") or "EUR",
                "status": receipt_data.get("processingStatus") or payload.get("status") or "processing",
                "created_at": payload.get("createdAt", ""),
                "thumbnail": receipt_data.get("imageUrl", ""),
                "pdf_url": payload.get("downloadUrl", ""),
            }
        )
    return entries


def _apply_admin_filters(items: list[dict], args) -> list[dict]:
    date_from = _to_iso_date(args.get("date_from"))
    date_to = _to_iso_date(args.get("date_to"))
    month = args.get("month")
    year = args.get("year")
    merchant = (args.get("merchant") or "").strip().lower()
    category = (args.get("category") or "").strip()
    employee_id = (args.get("employee_id") or "").strip()
    host = (args.get("host") or "").strip().lower()
    occasion = (args.get("occasion") or "").strip().lower()
    status = (args.get("status") or "").strip().lower()
    amount_min = _to_float(args.get("amount_min"))
    amount_max = _to_float(args.get("amount_max")) if args.get("amount_max") not in (None, "") else None

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
        if year and item_date and str(year) != item_date[:4]:
            continue
        if merchant and merchant not in (item.get("merchant") or "").lower():
            continue
        if category and category in ALLOWED_CATEGORIES and item.get("category") != category:
            continue
        if employee_id and item.get("user_id") != employee_id:
            continue
        if host and host not in (item.get("host") or "").lower():
            continue
        if occasion and occasion not in (item.get("occasion") or "").lower():
            continue
        if status and status != (item.get("status") or "").lower():
            continue
        amount = _to_float(item.get("total_amount"))
        if amount < amount_min:
            continue
        if amount_max is not None and amount > amount_max:
            continue
        filtered.append(item)
    return filtered


def _apply_admin_sort(items: list[dict], sort_key: str | None) -> list[dict]:
    key = sort_key or "newest"
    if key == "oldest":
        return sorted(items, key=lambda i: i.get("created_at") or "")
    if key == "amount_high":
        return sorted(items, key=lambda i: _to_float(i.get("total_amount")), reverse=True)
    if key == "amount_low":
        return sorted(items, key=lambda i: _to_float(i.get("total_amount")))
    return sorted(items, key=lambda i: i.get("created_at") or "", reverse=True)


def _paginate(items: list[dict], page: int, per_page: int) -> tuple[list[dict], int]:
    total = len(items)
    start = (page - 1) * per_page
    end = start + per_page
    return items[start:end], total


def _export_rows(rows: list[dict]) -> list[dict]:
    export = []
    for row in rows:
        export.append(
            {
                "document_id": row.get("document_id", ""),
                "employee_name": row.get("employee_name", ""),
                "email": row.get("email", ""),
                "merchant": row.get("merchant", ""),
                "date": row.get("date", ""),
                "category": row.get("category", ""),
                "host": row.get("host", ""),
                "occasion": row.get("occasion", ""),
                "total_amount": row.get("total_amount", 0),
                "currency": row.get("currency", ""),
                "status": row.get("status", ""),
                "created_at": row.get("created_at", ""),
            }
        )
    return export


def _render_csv(rows: list[dict]) -> str:
    output = StringIO()
    fieldnames = [
        "document_id",
        "employee_name",
        "email",
        "merchant",
        "date",
        "category",
        "host",
        "occasion",
        "total_amount",
        "currency",
        "status",
        "created_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


def _render_xlsx(rows: list[dict]) -> bytes:
    try:
        from openpyxl import Workbook
    except ModuleNotFoundError:
        return _render_csv(rows).encode("utf-8")

    wb = Workbook()
    ws = wb.active
    ws.title = "Admin Archive"
    headers = [
        "document_id",
        "employee_name",
        "email",
        "merchant",
        "date",
        "category",
        "host",
        "occasion",
        "total_amount",
        "currency",
        "status",
        "created_at",
    ]
    ws.append(headers)
    for row in rows:
        ws.append([row.get(header, "") for header in headers])
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _clean_filter_query(filters: dict) -> dict:
    return {k: v for k, v in filters.items() if v not in (None, "", [])}


@admin_bp.get("/status")
@require_admin
def admin_status():
    return jsonify({"module": "admin", "status": "ok"})


@admin_bp.get("/archive")
@require_admin
def admin_archive():
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(max(int(request.args.get("per_page", 50)), 1), 200)
    all_entries = _load_admin_archive_entries()
    filtered = _apply_admin_filters(all_entries, request.args)
    sorted_entries = _apply_admin_sort(filtered, request.args.get("sort"))
    rows, total_count = _paginate(sorted_entries, page, per_page)

    total_amount = sum(_to_float(item.get("total_amount")) for item in filtered)
    avg_amount = total_amount / len(filtered) if filtered else 0
    employees = user_repository.get_all_users("admin")
    filters = {
        "date_from": request.args.get("date_from", ""),
        "date_to": request.args.get("date_to", ""),
        "month": request.args.get("month", ""),
        "year": request.args.get("year", ""),
        "merchant": request.args.get("merchant", ""),
        "category": request.args.get("category", ""),
        "employee_id": request.args.get("employee_id", ""),
        "host": request.args.get("host", ""),
        "occasion": request.args.get("occasion", ""),
        "status": request.args.get("status", ""),
        "amount_min": request.args.get("amount_min", ""),
        "amount_max": request.args.get("amount_max", ""),
        "sort": request.args.get("sort", "newest"),
    }
    clean_query = _clean_filter_query(filters)
    export_csv_url = url_for("admin.admin_archive_export")
    export_xlsx_url = url_for("admin.admin_archive_export")
    if clean_query:
        export_csv_url = f"{export_csv_url}?{urlencode(clean_query)}"
        export_xlsx_url = (
            f"{export_xlsx_url}?{urlencode({**clean_query, 'format': 'xlsx'})}"
        )
    prev_url = None
    next_url = None
    if page > 1:
        prev_url = url_for("admin.admin_archive") + "?" + urlencode(
            {**clean_query, "page": page - 1, "per_page": per_page}
        )
    if (page * per_page) < total_count:
        next_url = url_for("admin.admin_archive") + "?" + urlencode(
            {**clean_query, "page": page + 1, "per_page": per_page}
        )

    return render_template(
        "admin/archive.html",
        rows=rows,
        total_count=total_count,
        total_amount=total_amount,
        avg_amount=avg_amount,
        page=page,
        per_page=per_page,
        filters=filters,
        employees=employees,
        statuses=["uploaded", "ocr_processing", "ai_processing", "awaiting_review", "pdf_generated", "error", "completed"],
        has_next=(page * per_page) < total_count,
        prev_url=prev_url,
        next_url=next_url,
        export_csv_url=export_csv_url,
        export_xlsx_url=export_xlsx_url,
    )


@admin_bp.get("/archive/export")
@require_admin
def admin_archive_export():
    all_entries = _load_admin_archive_entries()
    filtered = _apply_admin_sort(
        _apply_admin_filters(all_entries, request.args), request.args.get("sort")
    )
    export_rows = _export_rows(filtered)
    file_date = datetime.now(timezone.utc).strftime("%Y%m%d")
    export_format = (request.args.get("format") or "csv").lower()
    audit_repository.create_log(
        user_id=g.user.get("uid"),
        action="admin_export_csv",
        details={"filters": dict(request.args), "format": export_format},
        request=request,
    )
    if export_format == "xlsx":
        payload = _render_xlsx(export_rows)
        return Response(
            payload,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="folio_admin_archive_{file_date}.xlsx"'
            },
        )

    csv_data = _render_csv(export_rows)
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="folio_admin_archive_{file_date}.csv"'
        },
    )


def _render_analytics_pdf(start_date: str, end_date: str, data: dict) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ModuleNotFoundError:
        summary = (
            f"Folio Analytics Report\nRange: {start_date} to {end_date}\n"
            f"Total Spending: {data.get('kpis', {}).get('total_spending', 0):.2f}\n"
            f"Submissions: {data.get('kpis', {}).get('total_submissions', 0)}\n"
        )
        return summary.encode("utf-8")

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    pdf.setFillColorRGB(0.06, 0.1, 0.16)
    pdf.rect(0, 0, width, height, stroke=0, fill=1)
    pdf.setFillColorRGB(0.96, 0.65, 0.14)
    pdf.setFont("Helvetica-Bold", 28)
    pdf.drawString(40, height - 80, "Folio Analytics Report")
    pdf.setFillColorRGB(0.94, 0.93, 0.9)
    pdf.setFont("Helvetica", 12)
    pdf.drawString(40, height - 110, f"Date range: {start_date} - {end_date}")
    pdf.showPage()

    kpis = data.get("kpis", {})
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(40, height - 60, "KPI Summary")
    pdf.setFont("Helvetica", 12)
    pdf.drawString(40, height - 90, f"Total Spending: EUR {kpis.get('total_spending', 0):.2f}")
    pdf.drawString(40, height - 110, f"Total Submissions: {kpis.get('total_submissions', 0)}")
    pdf.drawString(40, height - 130, f"Average Expense: EUR {kpis.get('average_expense', 0):.2f}")
    pdf.drawString(40, height - 150, f"Pending Review: {kpis.get('pending_review', 0)}")

    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(40, height - 190, "Top Merchants")
    y = height - 220
    pdf.setFont("Helvetica", 11)
    for merchant in data.get("top_merchants", [])[:10]:
        pdf.drawString(
            40,
            y,
            f"{merchant.get('merchant', '-')}: EUR {merchant.get('total', 0):.2f} ({merchant.get('count', 0)} submissions)",
        )
        y -= 18
        if y < 50:
            pdf.showPage()
            y = height - 60

    pdf.showPage()
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(40, height - 60, "Recent Activity")
    y = height - 90
    pdf.setFont("Helvetica", 10)
    for item in data.get("recent_activity", [])[:10]:
        pdf.drawString(
            40,
            y,
            f"{item.get('date', '')} | {item.get('employee', '')} | {item.get('merchant', '')} | EUR {item.get('amount', 0):.2f}",
        )
        y -= 16
    pdf.save()
    return buffer.getvalue()


@admin_bp.get("/analytics")
@require_admin
def admin_analytics():
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    data = analytics_service.get_dashboard_data(start_date=start_date, end_date=end_date)
    return render_template(
        "admin/analytics.html",
        dashboard_data=data,
        filters={"start_date": start_date or "", "end_date": end_date or ""},
    )


@admin_bp.get("/analytics/data")
@require_admin
def admin_analytics_data():
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    data = analytics_service.get_dashboard_data(start_date=start_date, end_date=end_date)
    return jsonify({"success": True, "data": data})


@admin_bp.get("/analytics/export")
@require_admin
def admin_analytics_export():
    start_date = request.args.get("start_date") or datetime.now(timezone.utc).date().isoformat()
    end_date = request.args.get("end_date") or datetime.now(timezone.utc).date().isoformat()
    data = analytics_service.get_dashboard_data(start_date=start_date, end_date=end_date)
    payload = _render_analytics_pdf(start_date, end_date, data)
    file_date = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        payload,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="folio_analytics_report_{file_date}.pdf"'
        },
    )


def _user_to_view_row(user) -> dict:
    if firebase_config.db is None:
        submissions_count = 0
    else:
        submissions_count = len(
            list(
                firebase_config.db.collection("combined_documents")
                .where("userId", "==", user.id)
                .stream()
            )
        )
    return {
        "id": user.id,
        "name": f"{user.firstName} {user.surname}".strip(),
        "email": user.email,
        "role": user.role,
        "joined": user.createdAt.isoformat()[:10] if user.createdAt else "",
        "submissions": submissions_count,
        "disabled": bool(user.disabled),
    }


@admin_bp.get("/users")
@require_admin
def admin_users():
    name_filter = (request.args.get("name") or "").strip().lower()
    email_filter = (request.args.get("email") or "").strip().lower()
    role_filter = (request.args.get("role") or "").strip().lower()

    users = user_repository.get_all_users("admin")
    users.sort(key=lambda u: u.createdAt or datetime.min, reverse=True)
    rows = []
    for user in users:
        if name_filter and name_filter not in f"{user.firstName} {user.surname}".lower():
            continue
        if email_filter and email_filter not in (user.email or "").lower():
            continue
        if role_filter and role_filter in {"admin", "employee"} and user.role != role_filter:
            continue
        rows.append(_user_to_view_row(user))

    admin_count = sum(1 for user in users if user.role == "admin")
    employee_count = sum(1 for user in users if user.role == "employee")
    return render_template(
        "admin/users.html",
        users=rows,
        total_users=len(users),
        admin_count=admin_count,
        employee_count=employee_count,
        filters={"name": request.args.get("name", ""), "email": request.args.get("email", ""), "role": request.args.get("role", "")},
        current_admin_id=g.user.get("uid"),
    )


@admin_bp.get("/users/<user_id>")
@require_admin
def admin_user_detail(user_id: str):
    target = user_repository.get_user(user_id)
    if target is None:
        return jsonify({"success": False, "error": "User not found"}), 404

    submissions = []
    if firebase_config.db is not None:
        for doc in (
            firebase_config.db.collection("combined_documents")
            .where("userId", "==", user_id)
            .stream()
        ):
            payload = doc.to_dict() or {}
            submissions.append(
                {
                    "documentId": payload.get("id", doc.id),
                    "merchant": payload.get("merchant", "-"),
                    "category": payload.get("category", "Other"),
                    "amount": _to_float(payload.get("totalAmount")),
                    "status": payload.get("status", "processing"),
                    "createdAt": payload.get("createdAt", ""),
                    "downloadUrl": payload.get("downloadUrl", ""),
                }
            )
    submissions.sort(key=lambda item: item.get("createdAt") or "", reverse=True)

    activity = [entry.__dict__ for entry in audit_repository.get_user_logs(user_id=user_id, limit=100)]

    return render_template(
        "admin/user_detail.html",
        user=target,
        submissions=submissions,
        activity=activity[:100],
    )


@admin_bp.post("/users/<user_id>/promote")
@require_admin
@limiter.limit(RATE_LIMITS["/admin/users/*/promote"], key_func=user_rate_limit_key)
def promote_user(user_id: str):
    target = user_repository.get_user(user_id)
    if target is None:
        return jsonify({"success": False, "message": "User not found"}), 404
    if user_id == g.user.get("uid"):
        return jsonify({"success": True, "message": "No changes applied."}), 200
    if target.role != "employee":
        return jsonify({"success": False, "message": "User is not eligible for promotion."}), 400

    user_repository.update_user(user_id, {"role": "admin"})
    if firebase_config.firebase_auth is not None:
        try:
            firebase_config.firebase_auth.set_custom_user_claims(user_id, {"role": "admin"})
        except Exception:
            pass
    audit_repository.create_log(
        user_id=g.user.get("uid"),
        action="user_promoted_to_admin",
        details={
            "targetUser": user_id,
            "previousRole": "employee",
            "newRole": "admin",
        },
        request=request,
    )
    return jsonify({"success": True, "message": "User promoted to admin"})


@admin_bp.post("/users/<user_id>/demote")
@require_admin
def demote_user(user_id: str):
    target = user_repository.get_user(user_id)
    if target is None:
        return jsonify({"success": False, "message": "User not found"}), 404
    if target.role != "admin":
        return jsonify({"success": False, "message": "Target user is not an admin."}), 400
    if user_id == g.user.get("uid"):
        return jsonify({"success": False, "message": "Admins cannot demote themselves."}), 400

    admins = [user for user in user_repository.get_all_users("admin") if user.role == "admin"]
    if len(admins) <= 1:
        return jsonify({"success": False, "message": "Cannot remove last admin"}), 400

    user_repository.update_user(user_id, {"role": "employee"})
    if firebase_config.firebase_auth is not None:
        try:
            firebase_config.firebase_auth.set_custom_user_claims(user_id, None)
            firebase_config.firebase_auth.revoke_refresh_tokens(user_id)
        except Exception:
            pass
    audit_repository.create_log(
        user_id=g.user.get("uid"),
        action="user_demoted",
        details={
            "targetUser": user_id,
            "previousRole": "admin",
            "newRole": "employee",
        },
        request=request,
    )
    return jsonify({"success": True})


@admin_bp.post("/users/<user_id>/deactivate")
@require_admin
def deactivate_user(user_id: str):
    target = user_repository.get_user(user_id)
    if target is None:
        return jsonify({"success": False, "message": "User not found"}), 404
    if user_id == g.user.get("uid"):
        return jsonify({"success": False, "message": "Cannot deactivate your own account."}), 400

    user_repository.update_user(user_id, {"disabled": True})
    if firebase_config.firebase_auth is not None:
        try:
            firebase_config.firebase_auth.update_user(user_id, disabled=True)
            firebase_config.firebase_auth.revoke_refresh_tokens(user_id)
        except Exception:
            pass
    audit_repository.create_log(
        user_id=g.user.get("uid"),
        action="user_deactivated",
        details={"targetUser": user_id},
        request=request,
    )
    return jsonify({"success": True})


@admin_bp.get("/audit-logs")
@require_admin
def admin_audit_logs():
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(max(int(request.args.get("per_page", 50)), 1), 200)
    user_filter = (request.args.get("user") or "").strip().lower()
    action_filter = (request.args.get("action") or "").strip()
    date_from = _to_iso_date(request.args.get("date_from"))
    date_to = _to_iso_date(request.args.get("date_to"))

    all_logs = audit_repository.get_all_logs(
        limit=2000,
        action_filter=action_filter or None,
        requester_role=g.user.get("role", "employee"),
    )
    users = {user.id: user for user in user_repository.get_all_users("admin")}
    rows = []
    for log in all_logs:
        user = users.get(log.userId)
        user_name = f"{user.firstName} {user.surname}".strip() if user else log.userId
        event_date = _to_iso_date(log.timestamp)
        if user_filter and user_filter not in (user_name or "").lower() and user_filter not in (log.userId or "").lower():
            continue
        if date_from and (not event_date or event_date < date_from):
            continue
        if date_to and (not event_date or event_date > date_to):
            continue
        rows.append(
            {
                "timestamp": log.timestamp,
                "user_name": user_name or "-",
                "action": log.action,
                "ip_address": log.ipAddress or "-",
                "details": log.details or {},
            }
        )

    total = len(rows)
    start = (page - 1) * per_page
    end = start + per_page
    paged_rows = rows[start:end]
    filters = {
        "user": request.args.get("user", ""),
        "action": request.args.get("action", ""),
        "date_from": request.args.get("date_from", ""),
        "date_to": request.args.get("date_to", ""),
    }
    prev_url = None
    next_url = None
    clean_filters = _clean_filter_query(filters)
    if page > 1:
        prev_url = url_for("admin.admin_audit_logs") + "?" + urlencode(
            {**clean_filters, "page": page - 1, "per_page": per_page}
        )
    if end < total:
        next_url = url_for("admin.admin_audit_logs") + "?" + urlencode(
            {**clean_filters, "page": page + 1, "per_page": per_page}
        )
    return render_template(
        "admin/audit_logs.html",
        rows=paged_rows,
        total_count=total,
        page=page,
        per_page=per_page,
        prev_url=prev_url,
        next_url=next_url,
        filters=filters,
        action_values=sorted(audit_repository.ALLOWED_ACTIONS),
    )
