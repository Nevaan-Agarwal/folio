from __future__ import annotations

from types import SimpleNamespace

import config.firebase as firebase_config


class _FakeDoc:
    exists = True


class _FakeDocumentRef:
    def get(self):
        return _FakeDoc()


class _FakeCollectionRef:
    def document(self, _doc_id: str):
        return _FakeDocumentRef()


class _FakeDB:
    def collection(self, _name: str):
        return _FakeCollectionRef()


class _FakeBucket:
    name = "folio-test-bucket"


def _mock_firebase(monkeypatch):
    state = {"initialized": False, "init_calls": 0}

    def fake_get_app():
        if not state["initialized"]:
            raise ValueError("No app initialized")
        return object()

    def fake_initialize_app(*_args, **_kwargs):
        state["initialized"] = True
        state["init_calls"] += 1
        return object()

    fake_db = _FakeDB()
    fake_bucket = _FakeBucket()
    fake_auth = SimpleNamespace(name="auth-client")

    monkeypatch.setattr(firebase_config, "get_app", fake_get_app)
    monkeypatch.setattr(firebase_config, "initialize_app", fake_initialize_app)
    monkeypatch.setattr(
        firebase_config.credentials,
        "Certificate",
        lambda path: {"path": path},
    )
    monkeypatch.setattr(firebase_config.firestore, "client", lambda: fake_db)
    monkeypatch.setattr(firebase_config.storage, "bucket", lambda: fake_bucket)
    monkeypatch.setattr(firebase_config, "auth", fake_auth)

    monkeypatch.setenv("FIREBASE_CREDENTIALS_PATH", "fake-service-account.json")
    monkeypatch.setenv("FIREBASE_STORAGE_BUCKET", "folio-test-bucket")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "folio-test-project")

    firebase_config.db = None
    firebase_config.bucket = None
    firebase_config.firebase_auth = None

    return state


def test_firebase_initializes_once(monkeypatch):
    state = _mock_firebase(monkeypatch)

    firebase_config.init_firebase()
    firebase_config.init_firebase()

    assert state["init_calls"] == 1
    assert firebase_config.db is not None
    assert firebase_config.bucket is not None
    assert firebase_config.firebase_auth is not None


def test_firestore_connection(monkeypatch):
    _mock_firebase(monkeypatch)
    firebase_config.init_firebase()

    doc = firebase_config.db.collection("_healthchecks").document("firebase-connection").get()
    assert doc.exists is True


def test_storage_connection(monkeypatch):
    _mock_firebase(monkeypatch)
    firebase_config.init_firebase()

    assert firebase_config.bucket.name == "folio-test-bucket"
