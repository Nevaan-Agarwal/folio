from datetime import datetime, timezone

from app import create_app
from models.document import CombinedDocumentModel
from models.form import FormModel
from models.receipt import ReceiptModel
from routes import archive as archive_routes
from routes import documents as documents_routes


def _session(client, uid="user-1", role="employee"):
    with client.session_transaction() as flask_session:
        flask_session["uid"] = uid
        flask_session["role"] = role
        flask_session["lang"] = "en"
        flask_session["name"] = "Alice"


def _doc(user_id="user-1"):
    return CombinedDocumentModel(
        id="doc-1",
        formId="form-1",
        receiptId="receipt-1",
        userId=user_id,
        filePath="combined_documents/user-1/doc-1/file.pdf",
        downloadUrl="https://example.com/file.pdf",
        emailSent=True,
        emailDeliveryStatus="sent",
        userEmail="alice@example.com",
    )


def _form():
    return FormModel(
        id="form-1",
        receiptId="receipt-1",
        userId="user-1",
        type="Hospitality Expense",
        expenseCategory="Business Meal",
        host="Alice",
        hostedPersons=["Bob"],
        occasion="Client Lunch",
        dateOfHospitality="2026-06-17",
        locationOfHospitality="Berlin",
        invoiceAmount=100.0,
        tip=10.0,
        totalAmount=110.0,
        merchant="Cafe Berlin",
        receiptNumber="R-1",
        date="2026-06-17",
        place="Berlin",
        status="approved",
    )


def _receipt():
    return ReceiptModel(
        id="receipt-1",
        userId="user-1",
        imageUrl="https://example.com/receipt.jpg",
        uploadedAt=datetime.now(timezone.utc),
        ocrText="raw ocr",
        ocrConfidence=82.0,
        merchant="Cafe Berlin",
        processingStatus="pdf_generated",
        reviewStatus="approved",
    )


def test_detail_view_requires_ownership(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(
        archive_routes.combined_document_repository,
        "get_document",
        lambda _doc_id: _doc(user_id="another-user"),
    )
    with app.test_client() as client:
        _session(client, uid="user-1", role="employee")
        response = client.get("/archive/doc-1")
    assert response.status_code == 403


def test_admin_can_view_any_document(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(
        archive_routes.combined_document_repository,
        "get_document",
        lambda _doc_id: _doc(user_id="another-user"),
    )
    monkeypatch.setattr(archive_routes.form_repository, "get_form", lambda _fid: _form())
    monkeypatch.setattr(archive_routes.receipt_repository, "get_receipt", lambda _rid: _receipt())
    monkeypatch.setattr(
        archive_routes,
        "_get_audit_timeline",
        lambda document_id, form_id, receipt_id, user_id: [],
    )
    with app.test_client() as client:
        _session(client, uid="admin-1", role="admin")
        response = client.get("/archive/doc-1")
    assert response.status_code == 200
    assert "Document Detail" in response.get_data(as_text=True)


def test_pdf_download_url_is_valid(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(archive_routes.combined_document_repository, "get_document", lambda _doc_id: _doc())
    monkeypatch.setattr(archive_routes.form_repository, "get_form", lambda _fid: _form())
    monkeypatch.setattr(archive_routes.receipt_repository, "get_receipt", lambda _rid: _receipt())
    monkeypatch.setattr(
        archive_routes,
        "_get_audit_timeline",
        lambda document_id, form_id, receipt_id, user_id: [],
    )
    with app.test_client() as client:
        _session(client)
        response = client.get("/archive/doc-1")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'href="https://example.com/file.pdf"' in body


def test_resend_email_works(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(
        documents_routes.combined_document_repository,
        "get_document",
        lambda _doc_id: _doc(),
    )
    monkeypatch.setattr(documents_routes.form_repository, "get_form", lambda _fid: _form())
    monkeypatch.setattr(
        documents_routes.user_repository,
        "get_user",
        lambda _uid: type("User", (), {"firstName": "Alice", "surname": "Meyer", "email": "alice@example.com", "language": "en"})(),
    )
    monkeypatch.setattr(
        documents_routes.firebase_config,
        "bucket",
        type("Bucket", (), {"blob": lambda self, path: type("Blob", (), {"download_as_bytes": lambda self: b"PDF"})()})(),
    )
    monkeypatch.setattr(
        documents_routes.email_service,
        "send_pdf_delivery",
        lambda **_kwargs: {"success": True, "message_id": "m-1", "error": None},
    )
    with app.test_client() as client:
        _session(client)
        response = client.post("/documents/doc-1/resend-email")
    assert response.status_code == 200
    assert response.get_json()["success"] is True


def test_audit_timeline_shows_correct_events(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(archive_routes.combined_document_repository, "get_document", lambda _doc_id: _doc())
    monkeypatch.setattr(archive_routes.form_repository, "get_form", lambda _fid: _form())
    monkeypatch.setattr(archive_routes.receipt_repository, "get_receipt", lambda _rid: _receipt())
    monkeypatch.setattr(
        archive_routes,
        "_get_audit_timeline",
        lambda document_id, form_id, receipt_id, user_id: [
            {"action": "receipt_uploaded", "timestamp": "2026-06-17T10:00:00Z", "isCurrent": False},
            {"action": "form_approved", "timestamp": "2026-06-17T10:02:00Z", "isCurrent": True},
        ],
    )
    with app.test_client() as client:
        _session(client)
        response = client.get("/archive/doc-1")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "receipt_uploaded" in body
    assert "form_approved" in body
