from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import create_app
from config import database as database_config


class _FakeDoc:
    def __init__(self, doc_id: str, payload: dict):
        self.id = doc_id
        self._payload = payload

    def to_dict(self):
        return dict(self._payload)


class _FakeCollection:
    def __init__(self, data: dict):
        self._data = data

    def stream(self):
        for doc_id, payload in self._data.items():
            yield _FakeDoc(doc_id, payload)


class _FakeDB:
    def __init__(self, seed: dict[str, dict[str, dict]]):
        self._seed = seed

    def collection(self, name: str):
        return _FakeCollection(self._seed.get(name, {}))


def _session(client, uid: str, role: str = "employee", name: str = "Alice"):
    with client.session_transaction() as flask_session:
        flask_session["uid"] = uid
        flask_session["role"] = role
        flask_session["lang"] = "en"
        flask_session["name"] = name
        flask_session["email"] = f"{uid}@example.com"


def _seed_data():
    now = datetime.now(timezone.utc)
    month_iso = now.isoformat()
    old_iso = (now - timedelta(days=40)).isoformat()
    seed = {
        "users": {
            "u1": {"firstName": "Alice"},
            "u2": {"firstName": "Bob"},
            "admin": {"firstName": "Admin"},
        },
        "receipts": {
            "r1": {"userId": "u1", "processingStatus": "awaiting_review", "merchant": "Cafe One"},
            "r2": {"userId": "u1", "processingStatus": "pdf_generated", "merchant": "Cafe Two"},
            "r3": {"userId": "u1", "processingStatus": "completed", "merchant": "Cafe Three"},
            "r4": {"userId": "u1", "processingStatus": "completed", "merchant": "Cafe Four"},
            "r5": {"userId": "u1", "processingStatus": "completed", "merchant": "Cafe Five"},
            "r6": {"userId": "u1", "processingStatus": "completed", "merchant": "Cafe Six"},
            "r7": {"userId": "u2", "processingStatus": "awaiting_review", "merchant": "Other Co"},
        },
        "forms": {
            "r1": {"userId": "u1", "receiptId": "r1", "merchant": "Cafe One"},
            "r2": {"userId": "u1", "receiptId": "r2", "merchant": "Cafe Two"},
            "r7": {"userId": "u2", "receiptId": "r7", "merchant": "Other Co"},
        },
        "combined_documents": {
            "d1": {"id": "d1", "userId": "u1", "receiptId": "r1", "merchant": "Cafe One", "category": "Restaurant", "totalAmount": 100.5, "createdAt": month_iso},
            "d2": {"id": "d2", "userId": "u1", "receiptId": "r2", "merchant": "Cafe Two", "category": "Business Meal", "totalAmount": 80, "createdAt": month_iso},
            "d3": {"id": "d3", "userId": "u1", "receiptId": "r3", "merchant": "Cafe Three", "category": "Travel", "totalAmount": 30, "createdAt": month_iso},
            "d4": {"id": "d4", "userId": "u1", "receiptId": "r4", "merchant": "Cafe Four", "category": "Hotel", "totalAmount": 55, "createdAt": month_iso},
            "d5": {"id": "d5", "userId": "u1", "receiptId": "r5", "merchant": "Cafe Five", "category": "Other", "totalAmount": 20, "createdAt": month_iso},
            "d6": {"id": "d6", "userId": "u1", "receiptId": "r6", "merchant": "Cafe Six", "category": "Other", "totalAmount": 10, "createdAt": old_iso},
            "d7": {"id": "d7", "userId": "u2", "receiptId": "r7", "merchant": "Other Co", "category": "Other", "totalAmount": 42, "createdAt": month_iso},
        },
    }
    return seed


def test_dashboard_loads_for_employee(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(database_config, "db", _FakeDB(_seed_data()))
    with app.test_client() as client:
        _session(client, "u1", "employee", "Alice")
        response = client.get("/dashboard")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Good" in body
    assert "Recent Submissions" in body
    assert "Company Overview" not in body
    assert "Welcome to Folio" in body


def test_dashboard_loads_for_admin(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(database_config, "db", _FakeDB(_seed_data()))
    with app.test_client() as client:
        _session(client, "admin", "admin", "Admin")
        response = client.get("/dashboard")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Company Overview" in body
    assert "View Analytics" in body
    assert "€ 327.50" in body


def test_dashboard_hides_onboarding_after_completion(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(database_config, "db", _FakeDB(_seed_data()))
    with app.test_client() as client:
        _session(client, "u1", "employee", "Alice")
        with client.session_transaction() as flask_session:
            flask_session["onboarding_completed"] = True
        response = client.get("/dashboard")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'id="onboardingBackdrop" hidden' in body


def test_pending_review_banner_not_rendered(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(database_config, "db", _FakeDB(_seed_data()))
    with app.test_client() as client:
        _session(client, "u1", "employee", "Alice")
        response = client.get("/dashboard")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "form(s) awaiting review" not in body
    assert "Review Now" not in body


def test_recent_submissions_limited_to_5(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(database_config, "db", _FakeDB(_seed_data()))
    with app.test_client() as client:
        _session(client, "u1", "employee", "Alice")
        response = client.get("/dashboard")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert body.count('class="recent-row"') == 5


def test_stats_calculate_correctly(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(database_config, "db", _FakeDB(_seed_data()))
    with app.test_client() as client:
        _session(client, "u1", "employee", "Alice")
        response = client.get("/dashboard")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Submissions this month" in body
    assert ">5<" in body  # only current-month submissions
    assert "€ 285.50" in body
