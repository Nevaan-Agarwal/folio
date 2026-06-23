from __future__ import annotations

from app import create_app
from config import database as database_config
from repositories import audit_repository


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


class _FakeQuery:
    def __init__(self, storage, collection_name, filters=None):
        self._storage = storage
        self._collection_name = collection_name
        self._filters = filters or []

    def where(self, field, operator, value):
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
            if include:
                yield _FakeDocSnapshot(doc_id, payload)

    def document(self, doc_id=None):
        resolved = doc_id or f"doc-{len(self._storage.get(self._collection_name, {})) + 1}"
        return _FakeDocRef(self._storage, self._collection_name, resolved)

    def add(self, payload):
        collection = self._storage.setdefault(self._collection_name, {})
        doc_id = f"doc-{len(collection) + 1}"
        collection[doc_id] = dict(payload)
        return _FakeDocRef(self._storage, self._collection_name, doc_id)


class _FakeDB:
    def __init__(self):
        self.storage = {}

    def collection(self, name):
        return _FakeQuery(self.storage, name)


class _ImmediateThread:
    def __init__(self, target=None, args=(), daemon=False):
        self._target = target
        self._args = args

    def start(self):
        if self._target:
            self._target(*self._args)


def test_notification_created_on_ocr_complete(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(database_config, "db", fake_db)
    monkeypatch.setattr(audit_repository.threading, "Thread", _ImmediateThread)

    audit_repository.create_log(
        user_id="user-1",
        action="ocr_completed",
        details={"receiptId": "r1", "confidence": 87.0},
    )
    notifications = audit_repository.get_user_notifications("user-1")
    assert any(item.action == "ocr_completed" for item in notifications)


def test_notification_created_on_pdf_generated(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(database_config, "db", fake_db)
    monkeypatch.setattr(audit_repository.threading, "Thread", _ImmediateThread)

    audit_repository.create_log(
        user_id="user-1",
        action="pdf_generated",
        details={"documentId": "doc-1"},
    )
    notifications = audit_repository.get_user_notifications("user-1")
    assert any(item.action == "pdf_generated" for item in notifications)


def test_unread_count_accurate(monkeypatch):
    fake_db = _FakeDB()
    fake_db.storage["auditLogs"] = {
        "n1": {"userId": "user-1", "action": "pdf_generated", "readBy": []},
        "n2": {"userId": "user-1", "action": "email_sent", "readBy": ["user-1"]},
        "n3": {"userId": "user-1", "action": "ocr_completed", "readBy": []},
    }
    monkeypatch.setattr(database_config, "db", fake_db)

    assert audit_repository.get_unread_notification_count("user-1") == 2


def test_mark_as_read_works(monkeypatch):
    app = create_app("testing")
    fake_db = _FakeDB()
    fake_db.storage["auditLogs"] = {
        "notif-1": {"userId": "user-1", "action": "pdf_generated", "readBy": []}
    }
    monkeypatch.setattr(database_config, "db", fake_db)

    with app.test_client() as client:
        with client.session_transaction() as flask_session:
            flask_session["uid"] = "user-1"
            flask_session["role"] = "employee"
            flask_session["lang"] = "en"
            flask_session["name"] = "Alice"
        response = client.post("/api/notifications/notif-1/read")

    assert response.status_code == 200
    assert "user-1" in fake_db.storage["auditLogs"]["notif-1"]["readBy"]


def test_toast_shows_on_flask_flash_message():
    app = create_app("testing")
    with app.test_client() as client:
        with client.session_transaction() as flask_session:
            flask_session["uid"] = "user-1"
            flask_session["email"] = "a@example.com"
            flask_session["role"] = "employee"
            flask_session["lang"] = "en"
            flask_session["name"] = "Alice"
        response = client.get("/auth/logout", follow_redirects=True)

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "window.__folioFlashToasts" in body
    assert "window.showToast" in body
