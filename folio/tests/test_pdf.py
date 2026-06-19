from __future__ import annotations

from datetime import datetime, timezone

import pytest

reportlab = pytest.importorskip("reportlab")

from config import firebase as firebase_config
from services import pdf_service


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xff\xff?"
    b"\x00\x05\xfe\x02\xfeA\xdd\x8d\xb1\x00\x00\x00\x00IEND\xaeB`\x82"
)


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
    def __init__(self, seed):
        self.storage = seed

    def collection(self, name):
        return _FakeCollection(self.storage, name)


class _FakeBlob:
    def __init__(self, path):
        self.path = path
        self.uploaded_bytes = b""
        self.public_url = f"https://storage.local/{path}"

    def upload_from_string(self, payload, content_type=None):
        self.uploaded_bytes = payload

    def generate_signed_url(self, expiration):
        return f"https://signed.local/{self.path}"


class _FakeBucket:
    def __init__(self):
        self.blobs = {}

    def blob(self, path):
        blob = _FakeBlob(path)
        self.blobs[path] = blob
        return blob


class _FakeUrlResponse:
    def read(self):
        return PNG_BYTES

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _setup_service(monkeypatch):
    seed = {
        "forms": {
            "form-1": {
                "id": "form-1",
                "type": "Hospitality Expense",
                "expenseCategory": "Business Meal",
                "receiptNumber": "RB-9",
                "merchant": "Cafe Berlin",
                "dateOfHospitality": "2026-06-17",
                "locationOfHospitality": "Berlin",
                "host": "Alice",
                "occasion": "Client meeting",
                "hostedPersons": ["Bob", "Chris"],
                "invoiceAmount": 100.0,
                "tip": 10.0,
                "totalAmount": 110.0,
            }
        },
        "receipts": {
            "receipt-1": {
                "id": "receipt-1",
                "imageUrl": "https://example.local/receipt.png",
                "processingStatus": "pdf_generation",
            }
        },
        "users": {
            "user-1": {
                "firstName": "Alice",
                "surname": "Meyer",
                "email": "alice@example.com",
            }
        },
    }
    fake_db = _FakeDB(seed)
    fake_bucket = _FakeBucket()
    monkeypatch.setattr(firebase_config, "db", fake_db)
    monkeypatch.setattr(firebase_config, "bucket", fake_bucket)
    monkeypatch.setattr(pdf_service.urllib.request, "urlopen", lambda _url, timeout=10: _FakeUrlResponse())

    class _ImmediateThread:
        def __init__(self, target=None, args=(), daemon=False):
            self._target = target
            self._args = args

        def start(self):
            if self._target:
                self._target(*self._args)

    monkeypatch.setattr(pdf_service.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        pdf_service.email_service,
        "send_pdf_delivery",
        lambda **kwargs: {"success": True, "message_id": "msg-pdf", "error": None},
    )
    monkeypatch.setenv("RESEND_API_KEY", "test")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "Folio <onboarding@resend.dev>")
    service = pdf_service.PdfService()
    return service, fake_db, fake_bucket


def _run_generation(monkeypatch):
    service, fake_db, fake_bucket = _setup_service(monkeypatch)
    url = service.generate_pdf("form-1", "receipt-1", "user-1")
    blob = list(fake_bucket.blobs.values())[0]
    return url, blob, fake_db


def test_pdf_generates_two_pages(monkeypatch):
    _url, blob, _db = _run_generation(monkeypatch)
    content = blob.uploaded_bytes.decode("latin-1", errors="ignore")
    page_markers = content.count("/Type /Page") - content.count("/Type /Pages")
    assert page_markers == 2


def test_pdf_contains_form_data(monkeypatch):
    _url, blob, _db = _run_generation(monkeypatch)
    content = blob.uploaded_bytes.decode("latin-1", errors="ignore")
    assert "Cafe Berlin" in content
    assert "Client meeting" in content
    assert "Business Meal" in content


def test_pdf_includes_receipt_image(monkeypatch):
    _url, blob, _db = _run_generation(monkeypatch)
    content = blob.uploaded_bytes.decode("latin-1", errors="ignore")
    assert "/Subtype /Image" in content


def test_pdf_uploads_to_firebase_storage(monkeypatch):
    url, _blob, _db = _run_generation(monkeypatch)
    assert url.startswith("https://signed.local/combined_documents/user-1/")


def test_pdf_updates_firestore_status(monkeypatch):
    _url, _blob, db = _run_generation(monkeypatch)
    receipt = db.storage["receipts"]["receipt-1"]
    assert receipt["processingStatus"] == "pdf_generated"
    assert receipt["pdfUrl"].startswith("https://signed.local/")


def test_pdf_filename_format_correct(monkeypatch):
    _url, blob, _db = _run_generation(monkeypatch)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert blob.path.endswith(f"folio_{today}.pdf")
    assert blob.path.startswith("combined_documents/user-1/form-1-receipt-1/")
