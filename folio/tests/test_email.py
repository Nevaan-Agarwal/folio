from __future__ import annotations

from app import create_app
from config import database as database_config
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
    context = service._template_context("Alice", _form_data(), "https://example.com/pdf", "doc-10")
    assert (
        service._build_subject(_form_data(), context)
        == "Your Folio Expense Report — Cafe Berlin 2026-06-17"
    )


def test_german_user_receives_german_subject():
    service = EmailService()
    context = service._template_context("Alice", _form_data(language="de"), "https://example.com/pdf", "doc-11")
    assert (
        service._build_subject(_form_data(language="de"), context)
        == "Ihr Folio Spesenbericht — Cafe Berlin 2026-06-17"
    )


def test_custom_email_templates_use_employee_and_document_placeholders(monkeypatch):
    monkeypatch.setenv(
        "FOLIO_EMAIL_SUBJECT_TEMPLATE",
        "Expense update for {{ employee_name }}: {{ document_merchant }} ({{ document_date }})",
    )
    monkeypatch.setenv(
        "FOLIO_EMAIL_TEXT_TEMPLATE",
        "Hi {{ employee_name }}, your document {{ document_id }} from {{ document_merchant }} totals {{ document_total }}.",
    )
    service = EmailService()
    context = service._template_context("Alice Meyer", _form_data(), "https://example.com/pdf", "doc-12")

    subject = service._build_subject(_form_data(), context)
    plain_text = service._build_plain_text(context)

    assert subject == "Expense update for Alice Meyer: Cafe Berlin (2026-06-17)"
    assert "Hi Alice Meyer, your document doc-12 from Cafe Berlin totals EUR 119.95." in plain_text


def test_resend_configuration_rejects_invalid_api_key_format(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "invalid-key")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "Folio <onboarding@resend.dev>")
    service = EmailService()

    _api_key, _from_email, config_error = service._resolve_resend_config()

    assert config_error is not None
    assert "starts with re_" in config_error


def test_sender_restriction_retries_with_onboarding_sender(monkeypatch):
    service = EmailService()
    monkeypatch.setattr(service, "_resolve_resend_config", lambda: ("re_test_key", "owner@gmail.com", None))
    calls = {"from_values": []}

    def _fake_request(api_key, payload):
        calls["from_values"].append(payload.get("from"))
        if payload.get("from") == "owner@gmail.com":
            return None, "Resend error: 403 sender not verified"
        return "msg-fallback", None

    monkeypatch.setattr(service, "_perform_resend_request", _fake_request)
    message_id, send_error = service._send_via_resend(
        to_email="alice@example.com",
        subject="Test",
        plain_text="Hello",
        html_content="<p>Hello</p>",
        pdf_bytes=b"%PDF",
        filename="file.pdf",
    )

    assert message_id == "msg-fallback"
    assert send_error is None
    assert calls["from_values"] == ["owner@gmail.com", "Folio <onboarding@resend.dev>"]


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


def test_resend_endpoint_disabled(monkeypatch):
    app = create_app("testing")
    with app.test_client() as client:
        with client.session_transaction() as flask_session:
            flask_session["uid"] = "user-1"
            flask_session["role"] = "employee"
            flask_session["lang"] = "en"
            flask_session["name"] = "Alice"
        response = client.post("/documents/doc-3/resend-email")

    assert response.status_code == 410
    assert response.get_json()["success"] is False


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
