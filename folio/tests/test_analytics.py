from datetime import datetime, timedelta, timezone

from app import create_app
from config import firebase as firebase_config
from routes import admin as admin_routes
from services.analytics_service import AnalyticsService


class _FakeDocSnapshot:
    def __init__(self, doc_id, payload):
        self.id = doc_id
        self._payload = payload
        self.exists = payload is not None

    def to_dict(self):
        return dict(self._payload or {})


class _FakeDocRef:
    def __init__(self, storage, collection_name, doc_id):
        self._storage = storage
        self._collection_name = collection_name
        self._doc_id = doc_id

    def get(self):
        payload = self._storage.get(self._collection_name, {}).get(self._doc_id)
        return _FakeDocSnapshot(self._doc_id, payload)

    def set(self, payload, merge=False):
        collection = self._storage.setdefault(self._collection_name, {})
        if merge and self._doc_id in collection:
            merged = dict(collection[self._doc_id])
            merged.update(payload)
            collection[self._doc_id] = merged
        else:
            collection[self._doc_id] = dict(payload)


class _FakeCollection:
    def __init__(self, storage, name):
        self._storage = storage
        self._name = name

    def document(self, doc_id):
        return _FakeDocRef(self._storage, self._name, doc_id)

    def where(self, *_args, **_kwargs):
        return self

    def stream(self):
        collection = self._storage.setdefault(self._name, {})
        snapshots = []
        for doc_id, payload in collection.items():
            snapshots.append(_FakeDocSnapshot(doc_id, payload))
        return snapshots


class _FakeDB:
    def __init__(self):
        self.storage = {}

    def collection(self, name):
        return _FakeCollection(self.storage, name)


def _admin_session(client, role="admin"):
    with client.session_transaction() as flask_session:
        flask_session["uid"] = "admin-1" if role == "admin" else "user-1"
        flask_session["role"] = role
        flask_session["lang"] = "en"
        flask_session["name"] = "Admin" if role == "admin" else "Employee"


def _doc(amount, category="Business Meal", user_id="u1", created_at="2026-06-10T10:00:00+00:00"):
    return {
        "id": f"doc-{amount}",
        "formId": "",
        "receiptId": "",
        "userId": user_id,
        "merchant": "Cafe Berlin",
        "category": category,
        "host": "Alice",
        "occasion": "Meeting",
        "totalAmount": amount,
        "currency": "EUR",
        "status": "pdf_generated",
        "createdAt": created_at,
    }


def test_analytics_calculates_total_correctly(monkeypatch):
    service = AnalyticsService()
    monkeypatch.setattr(service, "_load_documents", lambda start, end: [_doc(100), _doc(50)])
    monkeypatch.setattr(service, "_cache_key", lambda start, end: "k")
    monkeypatch.setattr(service, "_should_refresh", lambda payload: True)
    monkeypatch.setattr(firebase_config, "db", _FakeDB())
    monkeypatch.setattr(
        admin_routes.user_repository,
        "get_all_users",
        lambda role: [],
    )
    monkeypatch.setattr(
        __import__("services.analytics_service", fromlist=["user_repository"]).user_repository,
        "get_all_users",
        lambda role: [],
    )
    data = service.get_dashboard_data("2026-01-01", "2026-12-31")
    assert data["kpis"]["total_spending"] == 150.0
    assert data["kpis"]["total_submissions"] == 2


def test_spending_by_category_sums_correctly(monkeypatch):
    service = AnalyticsService()
    monkeypatch.setattr(service, "_load_documents", lambda start, end: [_doc(100, "Travel"), _doc(40, "Travel"), _doc(20, "Hotel")])
    monkeypatch.setattr(service, "_cache_key", lambda start, end: "k")
    monkeypatch.setattr(service, "_should_refresh", lambda payload: True)
    monkeypatch.setattr(firebase_config, "db", _FakeDB())
    monkeypatch.setattr(
        __import__("services.analytics_service", fromlist=["user_repository"]).user_repository,
        "get_all_users",
        lambda role: [],
    )
    data = service.get_dashboard_data("2026-01-01", "2026-12-31")
    assert data["spending_by_category"]["Travel"] == 140.0
    assert data["spending_by_category"]["Hotel"] == 20.0


def test_employee_analytics_restricted_to_admin():
    app = create_app("testing")
    with app.test_client() as client:
        _admin_session(client, role="employee")
        response = client.get("/admin/analytics")
    assert response.status_code == 403


def test_date_filter_applied_correctly(monkeypatch):
    app = create_app("testing")
    calls = {}
    monkeypatch.setattr(
        admin_routes.analytics_service,
        "get_dashboard_data",
        lambda start_date=None, end_date=None: calls.update({"start": start_date, "end": end_date}) or {"kpis": {}, "spending_by_category": {}, "spending_by_month": {}, "spending_by_employee": {}, "top_merchants": [], "submission_volume_by_month": {}, "hospitality_breakdown": {}, "recent_activity": []},
    )
    with app.test_client() as client:
        _admin_session(client, role="admin")
        response = client.get("/admin/analytics?start_date=2026-01-01&end_date=2026-06-30")
    assert response.status_code == 200
    assert calls["start"] == "2026-01-01"
    assert calls["end"] == "2026-06-30"


def test_cache_refreshes_after_one_hour(monkeypatch):
    service = AnalyticsService()
    fake_db = _FakeDB()
    key = "dashboard_2026-01-01_2026-12-31"
    fake_db.storage["analytics_cache"] = {
        key: {
            "generatedAt": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "data": {"kpis": {"total_spending": 1}},
        }
    }
    monkeypatch.setattr(firebase_config, "db", fake_db)
    calls = {"load": 0}
    monkeypatch.setattr(
        __import__("services.analytics_service", fromlist=["user_repository"]).user_repository,
        "get_all_users",
        lambda role: [],
    )
    monkeypatch.setattr(
        service,
        "_load_documents",
        lambda start, end: calls.__setitem__("load", calls["load"] + 1) or [_doc(10)],
    )
    data = service.get_dashboard_data("2026-01-01", "2026-12-31")
    assert calls["load"] == 1
    assert data["kpis"]["total_spending"] == 10.0
