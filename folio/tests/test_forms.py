from datetime import datetime, timezone
import time

from app import create_app
from models.form import FormModel
from models.receipt import ReceiptModel
from routes import forms as forms_routes


def _sample_form(**overrides):
    payload = {
        "id": "form-1",
        "receiptId": "receipt-1",
        "userId": "user-1",
        "type": "Hospitality Expense",
        "expenseCategory": "Business Meal",
        "host": "Alice",
        "hostedPersons": ["Bob", "Charlie"],
        "occasion": "Client lunch",
        "dateOfHospitality": "2026-06-17",
        "locationOfHospitality": "Berlin",
        "invoiceAmount": 100.0,
        "tip": 10.0,
        "totalAmount": 110.0,
        "merchant": "Hotel Berlin",
        "receiptNumber": "RB-1",
        "date": "2026-06-17",
        "place": "Berlin",
        "missingFields": [],
        "needsManualReview": False,
        "aiConfidence": {"overall": 0.9},
        "status": "draft",
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    }
    payload.update(overrides)
    return FormModel(**payload)


def _sample_receipt():
    return ReceiptModel(
        id="receipt-1",
        userId="user-1",
        imageUrl="https://example.com/receipt.jpg",
        uploadedAt=datetime.now(timezone.utc),
        ocrText="OCR TEXT",
        ocrConfidence=84.0,
        merchant="Hotel Berlin",
        address="Berlin",
        date="2026-06-17",
        currency="EUR",
        subtotal=100.0,
        tax=19.0,
        tip=10.0,
        total=110.0,
        receiptNumber="RB-1",
        processingStatus="awaiting_review",
        reviewStatus="draft",
    )


def _auth_session(client):
    with client.session_transaction() as flask_session:
        flask_session["uid"] = "user-1"
        flask_session["role"] = "employee"
        flask_session["lang"] = "en"
        flask_session["name"] = "Alice"


def test_form_pre_fills_from_ai_result(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(forms_routes.form_repository, "get_form_by_receipt", lambda _rid: _sample_form())
    monkeypatch.setattr(forms_routes.receipt_repository, "get_receipt", lambda _rid: _sample_receipt())

    with app.test_client() as client:
        _auth_session(client)
        response = client.get("/forms/receipt/receipt-1/review")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Business Meal" in body
    assert "Hotel Berlin" in body


def test_missing_fields_highlighted_correctly(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(
        forms_routes.form_repository,
        "get_form_by_receipt",
        lambda _rid: _sample_form(missingFields=["host", "occasion"]),
    )
    monkeypatch.setattr(forms_routes.receipt_repository, "get_receipt", lambda _rid: _sample_receipt())

    with app.test_client() as client:
        _auth_session(client)
        response = client.get("/forms/receipt/receipt-1/review")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "missingFields:" in body
    assert "host" in body
    assert "occasion" in body


def test_total_auto_calculates():
    app = create_app("testing")
    with app.test_client() as client:
        response = client.get("/static/js/form.js")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "invoice + tip" in body
    assert "recalcTotal" in body


def test_approve_validates_required_fields(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(
        forms_routes.form_repository,
        "get_form",
        lambda _fid: _sample_form(),
    )

    with app.test_client() as client:
        _auth_session(client)
        response = client.post(
            "/forms/form-1/approve",
            json={
                "dateOfHospitality": "",
                "locationOfHospitality": "Berlin",
                "host": "Alice",
                "occasion": "Meeting",
                "invoiceAmount": 100.0,
                "totalAmount": 110.0,
                "merchant": "Hotel Berlin",
            },
        )

    assert response.status_code == 400
    assert "missingFields" in response.get_json()


def test_draft_save_does_not_validate_required_fields(monkeypatch):
    app = create_app("testing")
    calls = {}
    monkeypatch.setattr(forms_routes.form_repository, "get_form", lambda _fid: _sample_form())
    monkeypatch.setattr(
        forms_routes.form_repository,
        "update_form",
        lambda form_id, data: calls.update({"form_id": form_id, "data": data}),
    )
    monkeypatch.setattr(forms_routes.audit_repository, "log_event", lambda _event: None)

    with app.test_client() as client:
        _auth_session(client)
        response = client.post(
            "/forms/form-1/save-draft",
            json={
                "type": "Hospitality Expense",
                "expenseCategory": "Travel",
                "host": "",
                "occasion": "",
            },
        )

    assert response.status_code == 200
    assert calls["form_id"] == "form-1"
    assert calls["data"]["status"] == "draft"
    assert "missingFields" not in calls["data"]


def test_approve_triggers_pdf_generation(monkeypatch):
    app = create_app("testing")
    calls = {"pdf_called": False}
    monkeypatch.setattr(forms_routes.form_repository, "get_form", lambda _fid: _sample_form())
    monkeypatch.setattr(forms_routes.form_repository, "approve_form", lambda _fid, _data: None)
    monkeypatch.setattr(forms_routes.audit_repository, "log_event", lambda _event: None)
    monkeypatch.setattr(
        forms_routes.pdf_service,
        "generate_pdf",
        lambda *_args: calls.__setitem__("pdf_called", True) or "ok",
    )

    with app.test_client() as client:
        _auth_session(client)
        response = client.post(
            "/forms/form-1/approve",
            json={
                "dateOfHospitality": "2026-06-17",
                "locationOfHospitality": "Berlin",
                "host": "Alice",
                "occasion": "Meeting",
                "invoiceAmount": 100.0,
                "totalAmount": 110.0,
                "merchant": "Hotel Berlin",
            },
        )

    assert response.status_code == 200
    for _ in range(20):
        if calls["pdf_called"]:
            break
        time.sleep(0.01)
    assert calls["pdf_called"] is True


def test_reject_resets_receipt_status(monkeypatch):
    app = create_app("testing")
    calls = {}
    monkeypatch.setattr(forms_routes.form_repository, "get_form", lambda _fid: _sample_form())
    monkeypatch.setattr(
        forms_routes.form_repository,
        "reject_form",
        lambda form_id, reason: calls.update({"form_id": form_id, "reason": reason}),
    )
    monkeypatch.setattr(forms_routes.audit_repository, "log_event", lambda _event: None)

    with app.test_client() as client:
        _auth_session(client)
        response = client.post("/forms/form-1/reject", json={"reason": "Unreadable receipt"})

    assert response.status_code == 200
    assert calls["form_id"] == "form-1"
    assert calls["reason"] == "Unreadable receipt"
    assert response.get_json()["redirectUrl"] == "/receipts/new"


def test_employee_cannot_approve_other_users_form(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(forms_routes.form_repository, "get_form", lambda _fid: _sample_form(userId="other-user"))

    with app.test_client() as client:
        _auth_session(client)
        response = client.post(
            "/forms/form-1/approve",
            json={
                "dateOfHospitality": "2026-06-17",
                "locationOfHospitality": "Berlin",
                "host": "Alice",
                "occasion": "Meeting",
                "invoiceAmount": 100.0,
                "totalAmount": 110.0,
                "merchant": "Hotel Berlin",
            },
        )

    assert response.status_code == 403


def test_approved_form_is_read_only(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(forms_routes.form_repository, "get_form_by_receipt", lambda _rid: _sample_form(status="approved"))
    monkeypatch.setattr(
        forms_routes.receipt_repository,
        "get_receipt",
        lambda _rid: _sample_receipt(),
    )

    with app.test_client() as client:
        _auth_session(client)
        response = client.get("/forms/receipt/receipt-1/review")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'data-read-only="true"' in body
    assert "Download PDF" not in body
