from __future__ import annotations

from app import create_app
from config import firebase as firebase_config


class _FakeDocSnapshot:
    def __init__(self, doc_id: str, payload: dict | None):
        self.id = doc_id
        self._payload = payload
        self.exists = payload is not None

    def to_dict(self):
        return dict(self._payload or {})


class _FakeDocRef:
    def __init__(self, storage: dict, collection_name: str, doc_id: str):
        self._storage = storage
        self._collection_name = collection_name
        self._doc_id = doc_id

    def get(self):
        payload = self._storage.get(self._collection_name, {}).get(self._doc_id)
        return _FakeDocSnapshot(self._doc_id, payload)


class _FakeQuery:
    def __init__(self, storage: dict, collection_name: str, filters=None):
        self._storage = storage
        self._collection_name = collection_name
        self._filters = filters or []

    def where(self, field: str, operator: str, value):
        return _FakeQuery(
            self._storage,
            self._collection_name,
            filters=[*self._filters, (field, operator, value)],
        )

    def stream(self):
        collection = self._storage.get(self._collection_name, {})
        for doc_id, payload in collection.items():
            include = True
            for field, operator, expected in self._filters:
                current = payload.get(field)
                if operator == "==" and current != expected:
                    include = False
                    break
                if operator == ">=" and not (current is not None and current >= expected):
                    include = False
                    break
                if operator == "<=" and not (current is not None and current <= expected):
                    include = False
                    break
            if include:
                yield _FakeDocSnapshot(doc_id, payload)

    def document(self, doc_id: str):
        return _FakeDocRef(self._storage, self._collection_name, doc_id)


class _FakeDB:
    def __init__(self, seed: dict[str, dict[str, dict]]):
        self.storage = seed

    def collection(self, name: str):
        return _FakeQuery(self.storage, name)


def _seed():
    return {
        "combined_documents": {
            "d1": {"id": "d1", "userId": "u1", "formId": "f1", "receiptId": "r1", "merchant": "Cafe Berlin", "category": "Business Meal", "totalAmount": 120.0, "createdAt": "2026-01-11T10:00:00+00:00"},
            "d2": {"id": "d2", "userId": "u1", "formId": "f2", "receiptId": "r2", "merchant": "Hotel Alpha", "category": "Hotel", "totalAmount": 320.0, "createdAt": "2026-01-05T10:00:00+00:00"},
            "d3": {"id": "d3", "userId": "u2", "formId": "f3", "receiptId": "r3", "merchant": "Travel Co", "category": "Travel", "totalAmount": 55.0, "createdAt": "2026-01-01T10:00:00+00:00"},
            "d4": {"id": "d4", "userId": "u1", "formId": "f4", "receiptId": "r4", "merchant": "Cafe Milano", "category": "Restaurant", "totalAmount": 90.0, "createdAt": "2026-02-01T10:00:00+00:00"},
            "d5": {"id": "d5", "userId": "u1", "formId": "f5", "receiptId": "r5", "merchant": "Office Store", "category": "Office Supplies", "totalAmount": 15.0, "createdAt": "2026-03-01T10:00:00+00:00"},
            "d6": {"id": "d6", "userId": "u1", "formId": "f6", "receiptId": "r6", "merchant": "Taxi AG", "category": "Transportation", "totalAmount": 28.0, "createdAt": "2026-04-01T10:00:00+00:00"},
            "d7": {"id": "d7", "userId": "u1", "formId": "f7", "receiptId": "r7", "merchant": "Client Bistro", "category": "Business Meal", "totalAmount": 77.0, "createdAt": "2026-05-01T10:00:00+00:00"},
        },
        "forms": {
            "f1": {"merchant": "Cafe Berlin", "occasion": "Client lunch", "host": "Alice", "expenseCategory": "Business Meal", "date": "2026-01-11", "receiptNumber": "RB-101"},
            "f2": {"merchant": "Hotel Alpha", "occasion": "Conference", "host": "Alice", "expenseCategory": "Hotel", "date": "2026-01-05", "receiptNumber": "HA-1"},
            "f3": {"merchant": "Travel Co", "occasion": "Trip", "host": "Bob", "expenseCategory": "Travel", "date": "2026-01-01", "receiptNumber": "TR-1"},
            "f4": {"merchant": "Cafe Milano", "occasion": "Meeting", "host": "Alice", "expenseCategory": "Restaurant", "date": "2026-02-01", "receiptNumber": "CM-1"},
            "f5": {"merchant": "Office Store", "occasion": "Supplies", "host": "Alice", "expenseCategory": "Office Supplies", "date": "2026-03-01", "receiptNumber": "OS-1"},
            "f6": {"merchant": "Taxi AG", "occasion": "Airport", "host": "Alice", "expenseCategory": "Transportation", "date": "2026-04-01", "receiptNumber": "TX-1"},
            "f7": {"merchant": "Client Bistro", "occasion": "Dinner", "host": "Alice", "expenseCategory": "Business Meal", "date": "2026-05-01", "receiptNumber": "CB-1"},
        },
        "receipts": {
            "r1": {"userId": "u1", "processingStatus": "pdf_generated"},
            "r2": {"userId": "u1", "processingStatus": "pdf_generated"},
            "r3": {"userId": "u2", "processingStatus": "awaiting_review"},
            "r4": {"userId": "u1", "processingStatus": "completed"},
            "r5": {"userId": "u1", "processingStatus": "completed"},
            "r6": {"userId": "u1", "processingStatus": "completed"},
            "r7": {"userId": "u1", "processingStatus": "completed"},
        },
    }


def _session(client, uid: str, role: str):
    with client.session_transaction() as flask_session:
        flask_session["uid"] = uid
        flask_session["role"] = role
        flask_session["lang"] = "en"
        flask_session["name"] = "Search User"


def test_search_returns_only_own_results_for_employee(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(firebase_config, "db", _FakeDB(_seed()))
    with app.test_client() as client:
        _session(client, uid="u1", role="employee")
        response = client.get("/api/search?q=travel")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total"] == 0


def test_admin_search_returns_all_results(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(firebase_config, "db", _FakeDB(_seed()))
    with app.test_client() as client:
        _session(client, uid="admin-1", role="admin")
        response = client.get("/api/search?q=travel")
    payload = response.get_json()
    assert payload["total"] == 1
    assert payload["results"][0]["merchant"] == "Travel Co"


def test_search_by_merchant_works(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(firebase_config, "db", _FakeDB(_seed()))
    with app.test_client() as client:
        _session(client, uid="u1", role="employee")
        response = client.get("/api/search?q=cafe")
    payload = response.get_json()
    merchants = {row["merchant"] for row in payload["results"]}
    assert "Cafe Berlin" in merchants
    assert "Cafe Milano" in merchants


def test_search_by_date_partial_works(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(firebase_config, "db", _FakeDB(_seed()))
    with app.test_client() as client:
        _session(client, uid="u1", role="employee")
        response = client.get("/api/search?q=2026-01")
    payload = response.get_json()
    assert payload["total"] == 2


def test_search_returns_correct_json_structure(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(firebase_config, "db", _FakeDB(_seed()))
    with app.test_client() as client:
        _session(client, uid="u1", role="employee")
        response = client.get('/api/search?q=hotel&filters={"status":"pdf_generated"}')
    payload = response.get_json()
    assert {"results", "total", "query"} <= set(payload.keys())
    if payload["results"]:
        assert {"id", "merchant", "date", "amount", "category", "status", "thumbnail"} <= set(
            payload["results"][0].keys()
        )


def test_autocomplete_returns_max_5_results(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(firebase_config, "db", _FakeDB(_seed()))
    with app.test_client() as client:
        _session(client, uid="u1", role="employee")
        response = client.get("/api/search?q=a&limit=5")
    payload = response.get_json()
    assert len(payload["results"]) <= 5
