"""Analytics aggregation service for admin dashboards."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from config import database as database_config
from repositories import form_repository, user_repository


class AnalyticsService:
    CACHE_COLLECTION = "analytics_cache"
    CACHE_TTL = timedelta(hours=1)

    def _to_float(self, value) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def _parse_iso(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    def _month_key(self, date_value: str | None) -> str:
        dt = self._parse_iso(date_value)
        if dt is None:
            return "unknown"
        return dt.strftime("%Y-%m")

    def _cache_key(self, start_date: str, end_date: str) -> str:
        return f"dashboard_{start_date}_{end_date}"

    def _should_refresh(self, payload: dict | None) -> bool:
        if not payload:
            return True
        generated_at = self._parse_iso(payload.get("generatedAt"))
        if generated_at is None:
            return True
        return datetime.now(timezone.utc) - generated_at >= self.CACHE_TTL

    def _default_range(self) -> tuple[str, str]:
        now = datetime.now(timezone.utc)
        start = datetime(now.year, 1, 1, tzinfo=timezone.utc).date().isoformat()
        end = now.date().isoformat()
        return start, end

    def _load_documents(self, start_date: str, end_date: str) -> list[dict]:
        if database_config.db is None:
            return []
        query = (
            database_config.db.collection("combined_documents")
            .where("createdAt", ">=", f"{start_date}T00:00:00+00:00")
            .where("createdAt", "<=", f"{end_date}T23:59:59+00:00")
        )
        docs = []
        for doc in query.stream():
            payload = doc.to_dict() or {}
            payload["id"] = doc.id
            docs.append(payload)
        return docs

    def _compute(self, docs: list[dict]) -> dict:
        users = {user.id: user for user in user_repository.get_all_users("admin")}
        spending_by_category = defaultdict(float)
        spending_by_month = defaultdict(float)
        spending_by_employee = defaultdict(lambda: {"name": "", "total": 0.0, "count": 0})
        submission_volume_by_month = defaultdict(int)
        merchant_totals = defaultdict(lambda: {"merchant": "", "total": 0.0, "count": 0})
        occasion_counts = defaultdict(int)
        recent_activity = []

        total_spending = 0.0
        total_submissions = 0
        pending_review = 0
        total_hospitality = 0.0

        for payload in docs:
            form_id = payload.get("formId")
            form = form_repository.get_form(form_id) if form_id else None
            form_data = form.__dict__ if form else {}

            amount = self._to_float(
                payload.get("totalAmount")
                or form_data.get("totalAmount")
            )
            merchant = (
                payload.get("merchant")
                or form_data.get("merchant")
                or "Unknown Merchant"
            )
            category = (
                payload.get("category")
                or form_data.get("expenseCategory")
                or "Other"
            )
            created_at = payload.get("createdAt")
            month_key = self._month_key(created_at)
            user_id = payload.get("userId", "")
            employee = users.get(user_id)
            employee_name = (
                f"{employee.firstName} {employee.surname}".strip()
                if employee
                else user_id or "Unknown"
            )
            status = payload.get("status", "")
            occasion = payload.get("occasion") or form_data.get("occasion") or ""

            total_spending += amount
            total_submissions += 1
            if status in {"awaiting_review", "ocr_processing", "ai_processing", "processing"}:
                pending_review += 1
            spending_by_category[category] += amount
            spending_by_month[month_key] += amount
            submission_volume_by_month[month_key] += 1

            spending_by_employee[user_id]["name"] = employee_name
            spending_by_employee[user_id]["total"] += amount
            spending_by_employee[user_id]["count"] += 1

            merchant_totals[merchant]["merchant"] = merchant
            merchant_totals[merchant]["total"] += amount
            merchant_totals[merchant]["count"] += 1

            if occasion:
                occasion_counts[occasion] += 1
                total_hospitality += amount

            recent_activity.append(
                {
                    "employee": employee_name,
                    "merchant": merchant,
                    "amount": amount,
                    "category": category,
                    "date": created_at[:10] if created_at else "",
                    "status": status or "processing",
                }
            )

        top_merchants = sorted(
            merchant_totals.values(),
            key=lambda item: item["total"],
            reverse=True,
        )[:10]

        for merchant in top_merchants:
            merchant["avg_amount"] = merchant["total"] / merchant["count"] if merchant["count"] else 0.0

        most_common_occasion = ""
        if occasion_counts:
            most_common_occasion = max(occasion_counts.items(), key=lambda pair: pair[1])[0]

        avg_expense = (total_spending / total_submissions) if total_submissions else 0.0
        avg_per_occasion = (total_hospitality / sum(occasion_counts.values())) if occasion_counts else 0.0

        recent_activity.sort(key=lambda item: item.get("date", ""), reverse=True)
        recent_activity = recent_activity[:10]

        return {
            "kpis": {
                "total_spending": total_spending,
                "total_submissions": total_submissions,
                "average_expense": avg_expense,
                "pending_review": pending_review,
            },
            "spending_by_category": dict(sorted(spending_by_category.items())),
            "spending_by_month": dict(sorted(spending_by_month.items())),
            "spending_by_employee": dict(spending_by_employee),
            "top_merchants": top_merchants,
            "submission_volume_by_month": dict(sorted(submission_volume_by_month.items())),
            "hospitality_breakdown": {
                "total_hospitality": total_hospitality,
                "avg_per_occasion": avg_per_occasion,
                "most_common_occasion": most_common_occasion,
            },
            "recent_activity": recent_activity,
        }

    def get_dashboard_data(self, start_date=None, end_date=None) -> dict:
        if database_config.db is None:
            return self._compute([])

        default_start, default_end = self._default_range()
        start_date = start_date or default_start
        end_date = end_date or default_end
        key = self._cache_key(start_date, end_date)
        cache_ref = database_config.db.collection(self.CACHE_COLLECTION).document(key)
        cache_doc = cache_ref.get()
        cache_payload = cache_doc.to_dict() if cache_doc.exists else None
        if cache_payload and not self._should_refresh(cache_payload):
            return cache_payload.get("data", {})

        docs = self._load_documents(start_date, end_date)
        data = self._compute(docs)
        cache_ref.set(
            {
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "startDate": start_date,
                "endDate": end_date,
                "data": data,
            }
        )
        return data


analytics_service = AnalyticsService()
