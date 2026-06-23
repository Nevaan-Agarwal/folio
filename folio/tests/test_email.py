from __future__ import annotations

from datetime import datetime, timezone

from app import create_app
from config import database as database_config
from models.document import CombinedDocumentModel
from models.form import FormModel
from routes import documents as documents_routes
from services.email_service import EmailService


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


class _FakeDB:
    def __init__(self):
        self.storage = {}

    def collection(self, name):
        return _FakeCollection(self.storage, name)


class _FakeBlob:
    def __init__(self):
        self.data = b"PDF"

    def download_as_bytes(self):
        return self.data


class _FakeBucket:
    def blob(self, _path):
        return _FakeBlob()


def _form_data(language="en"):
    return {
        "merchant": "Cafe Berlin",
        "date": "2026-06-17",
        "expenseCategory": "Business Meal",
        "totalAmount": 119.95,
        "language": language,
    }


def test_email_sends_with_pdf_attachment(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(database_config, "db", fake_db)
    service = EmailService()
    calls = {}

    def _fake_send(**kwargs):
        calls.update(kwargs)
        return ("msg-1", None)

    monkeypatch.setattr(service, "_send_via_resend", _fake_send)
    result = service.send_pdf_delivery(
        to_email="alice@example.com",
        user_name="Alice",
        form_data=_form_data(),
        pdf_download_url="https://example.com/pdf",
        pdf_bytes=b"%PDF-1.4 TEST",
        document_id="doc-1",
    )
    assert result["success"] is True
    assert calls["filename"].startswith("Folio_Expense_Cafe_Berlin_2026-06-17")
    assert calls["pdf_bytes"].startswith(b"%PDF")


def test_email_subject_correct_format():
    service = EmailService()
    assert (
        service._build_subject(_form_data())
        == "Your Folio Expense Report — Cafe Berlin 2026-06-17"
    )


def test_german_user_receives_german_subject():
    service = EmailService()
    assert (
        service._build_subject(_form_data(language="de"))
        == "Ihr Folio Spesenbericht — Cafe Berlin 2026-06-17"
    )


def test_delivery_status_saved_to_database(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(database_config, "db", fake_db)
    service = EmailService()
    monkeypatch.setattr(service, "_send_via_resend", lambda **_kwargs: ("msg-2", None))
    result = service.send_pdf_delivery(
        to_email="alice@example.com",
        user_name="Alice",
        form_data=_form_data(),
        pdf_download_url="https://example.com/pdf",
        pdf_bytes=b"%PDF-1.4 TEST",
        document_id="doc-2",
    )
    assert result["success"] is True
    saved = fake_db.storage["combined_documents"]["doc-2"]
    assert saved["emailDeliveryStatus"] == "sent"
    assert saved["emailMessageId"] == "msg-2"
    assert saved["emailSent"] is True


def test_resend_works_after_initial_failure(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(database_config, "bucket", _FakeBucket())
    monkeypatch.setattr(database_config, "db", _FakeDB())

    document = CombinedDocumentModel(
        id="doc-3",
        formId="form-1",
        receiptId="receipt-1",
        userId="user-1",
        filePath="combined_documents/user-1/doc-3/folio_20260617.pdf",
        downloadUrl="https://example.com/pdf",
        emailSent=True,
        emailDeliveryStatus="failed",
        userEmail="alice@example.com",
    )
    monkeypatch.setattr(documents_routes.combined_document_repository, "get_document", lambda _doc_id: document)
    monkeypatch.setattr(
        documents_routes.form_repository,
        "get_form",
        lambda _fid: FormModel(
            id="form-1",
            receiptId="receipt-1",
            userId="user-1",
            type="Hospitality Expense",
            expenseCategory="Business Meal",
            host="Alice",
            hostedPersons=["Bob"],
            occasion="Meeting",
            dateOfHospitality="2026-06-17",
            locationOfHospitality="Berlin",
            invoiceAmount=9.0,
            tip=1.0,
            totalAmount=10.0,
            merchant="Cafe Berlin",
            receiptNumber="R-1",
            date="2026-06-17",
            place="Berlin",
            createdAt=datetime.now(timezone.utc),
            updatedAt=datetime.now(timezone.utc),
        ),
    )
    monkeypatch.setattr(
        documents_routes.user_repository,
        "get_user",
        lambda _uid: type("UserObj", (), {"firstName": "Alice", "surname": "Meyer", "email": "alice@example.com", "language": "en"})(),
    )
    monkeypatch.setattr(
        documents_routes.email_service,
        "send_pdf_delivery",
        lambda **_kwargs: {"success": True, "message_id": "resend-1", "error": None},
    )

    with app.test_client() as client:
        with client.session_transaction() as flask_session:
            flask_session["uid"] = "user-1"
            flask_session["role"] = "employee"
            flask_session["lang"] = "en"
            flask_session["name"] = "Alice"
        response = client.post("/documents/doc-3/resend-email")

    assert response.status_code == 200
    assert response.get_json()["success"] is True


def test_pdf_attachment_is_correct_file(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(database_config, "db", fake_db)
    service = EmailService()
    calls = {}

    def _fake_send(**kwargs):
        calls.update(kwargs)
        return ("msg-3", None)

    monkeypatch.setattr(service, "_send_via_resend", _fake_send)
    service.send_pdf_delivery(
        to_email="alice@example.com",
        user_name="Alice",
        form_data=_form_data(),
        pdf_download_url="https://example.com/pdf",
        pdf_bytes=b"%PDF-1.7 Attachment",
        document_id="doc-4",
    )
    assert calls["filename"].endswith(".pdf")
    assert b"Attachment" in calls["pdf_bytes"]
