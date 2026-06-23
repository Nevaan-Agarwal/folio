from io import BytesIO

from app import create_app
from routes import receipts as receipts_routes


def _make_upload_payload(filename: str, content: bytes):
    return {"receipt": (BytesIO(content), filename)}


def _login_session(client):
    with client.session_transaction() as flask_session:
        flask_session["uid"] = "user-123"
        flask_session["role"] = "employee"
        flask_session["lang"] = "en"
        flask_session["name"] = "Alice"


def test_upload_creates_storage_file(monkeypatch):
    calls = {"storage": 0}
    app = create_app("testing")

    monkeypatch.setattr(
        receipts_routes,
        "upload_receipt_image",
        lambda file_object, user_id, receipt_id: calls.__setitem__("storage", calls["storage"] + 1)
        or "https://example.com/file.jpg",
    )
    monkeypatch.setattr(receipts_routes.receipt_repository, "create_receipt", lambda user_id, image_url, receipt_id=None: receipt_id or "receipt-1")
    monkeypatch.setattr(receipts_routes.audit_repository, "log_event", lambda event: None)

    with app.test_client() as client:
        _login_session(client)
        response = client.post(
            "/receipts/upload",
            data=_make_upload_payload("receipt.jpg", b"mock-jpg-bytes"),
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    assert calls["storage"] == 1


def test_upload_creates_database_record(monkeypatch):
    calls = {"database": 0}
    app = create_app("testing")

    monkeypatch.setattr(
        receipts_routes,
        "upload_receipt_image",
        lambda file_object, user_id, receipt_id: "https://example.com/file.jpg",
    )
    monkeypatch.setattr(
        receipts_routes.receipt_repository,
        "create_receipt",
        lambda user_id, image_url, receipt_id=None: calls.__setitem__("database", calls["database"] + 1) or (receipt_id or "receipt-1"),
    )
    monkeypatch.setattr(receipts_routes.audit_repository, "log_event", lambda event: None)

    with app.test_client() as client:
        _login_session(client)
        response = client.post(
            "/receipts/upload",
            data=_make_upload_payload("receipt.png", b"png-bytes"),
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    assert calls["database"] == 1


def test_upload_returns_before_ocr_completes(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(
        receipts_routes,
        "upload_receipt_image",
        lambda file_object, user_id, receipt_id: "https://example.com/file.webp",
    )
    monkeypatch.setattr(receipts_routes.receipt_repository, "create_receipt", lambda user_id, image_url, receipt_id=None: receipt_id or "receipt-1")
    monkeypatch.setattr(receipts_routes.audit_repository, "log_event", lambda event: None)

    # If synchronous processing were invoked during upload, this hook would fail.
    monkeypatch.setattr(
        receipts_routes,
        "start_ocr_processing",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("OCR should not run during upload")
        ),
        raising=False,
    )

    with app.test_client() as client:
        _login_session(client)
        response = client.post(
            "/receipts/upload",
            data=_make_upload_payload("receipt.webp", b"webp-bytes"),
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    assert response.get_json()["status"] == "uploaded"


def test_invalid_file_type_rejected():
    app = create_app("testing")
    with app.test_client() as client:
        _login_session(client)
        response = client.post(
            "/receipts/upload",
            data=_make_upload_payload("receipt.txt", b"plain text"),
            content_type="multipart/form-data",
        )
    assert response.status_code == 400
    assert "Invalid file type" in response.get_json()["message"]


def test_file_over_15mb_rejected():
    app = create_app("testing")
    large_content = b"x" * (15 * 1024 * 1024 + 1)
    with app.test_client() as client:
        _login_session(client)
        response = client.post(
            "/receipts/upload",
            data=_make_upload_payload("receipt.jpg", large_content),
            content_type="multipart/form-data",
        )
    assert response.status_code == 400
    assert "15MB" in response.get_json()["message"]


def test_status_set_to_uploaded_immediately(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(
        receipts_routes,
        "upload_receipt_image",
        lambda file_object, user_id, receipt_id: "https://example.com/file.jpg",
    )
    monkeypatch.setattr(receipts_routes.receipt_repository, "create_receipt", lambda user_id, image_url, receipt_id=None: receipt_id or "receipt-1")
    monkeypatch.setattr(receipts_routes.audit_repository, "log_event", lambda event: None)

    with app.test_client() as client:
        _login_session(client)
        response = client.post(
            "/receipts/upload",
            data=_make_upload_payload("receipt.jpg", b"jpg-bytes"),
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    assert response.get_json()["status"] == "uploaded"
