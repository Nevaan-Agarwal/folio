import time
from io import BytesIO
from types import SimpleNamespace

from app import create_app
from middleware import rate_limiter as rate_limiter_module
from models.user import UserModel
from repositories import audit_repository
from routes import auth as auth_routes
from routes import receipts as receipts_routes
from werkzeug.security import generate_password_hash


def _login_session(client):
    with client.session_transaction() as flask_session:
        flask_session["uid"] = "user-123"
        flask_session["role"] = "employee"
        flask_session["lang"] = "en"
        flask_session["name"] = "Alice"


def _make_upload_payload(filename: str, content: bytes):
    return {"receipt": (BytesIO(content), filename)}


def test_login_creates_audit_log(monkeypatch):
    app = create_app("testing")
    rate_limiter_module.limiter.reset()
    captured = []
    monkeypatch.setattr(
        auth_routes.user_repository,
        "get_user_by_email",
        lambda email: UserModel(
            id="uid-123",
            firstName="Alice",
            surname="Meyer",
            email="alice@example.com",
            passwordHash=generate_password_hash("StrongPass9"),
            role="employee",
            language="en",
            disabled=False,
            createdAt=None,
        ),
    )
    monkeypatch.setattr(
        auth_routes.audit_repository,
        "create_log",
        lambda user_id, action, details=None, request=None: captured.append(
            {"user_id": user_id, "action": action, "details": details or {}}
        ),
    )
    with app.test_client() as client:
        response = client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "StrongPass9"},
        )
    assert response.status_code == 200
    assert captured
    assert captured[0]["action"] == "user_login"


def test_receipt_upload_creates_audit_log(monkeypatch):
    app = create_app("testing")
    captured = []
    monkeypatch.setattr(
        receipts_routes,
        "upload_receipt_image",
        lambda file_object, user_id, receipt_id: "https://example.com/file.jpg",
    )
    monkeypatch.setattr(
        receipts_routes.receipt_repository,
        "create_receipt",
        lambda user_id, image_url, receipt_id=None: receipt_id or "receipt-1",
    )
    monkeypatch.setattr(
        receipts_routes.audit_repository,
        "create_log",
        lambda user_id, action, details=None, request=None: captured.append(
            {"user_id": user_id, "action": action, "details": details or {}}
        ),
    )
    with app.test_client() as client:
        _login_session(client)
        response = client.post(
            "/receipts/upload",
            data=_make_upload_payload("receipt.jpg", b"mock-jpg-bytes"),
            content_type="multipart/form-data",
        )
    assert response.status_code == 200
    assert any(entry["action"] == "receipt_uploaded" for entry in captured)


def test_rate_limit_blocks_after_threshold(monkeypatch):
    app = create_app("testing")
    auth_routes.FAILED_LOGIN_ATTEMPTS.clear()
    monkeypatch.setattr(auth_routes, "_too_many_failed_attempts", lambda _key: False)
    monkeypatch.setattr(auth_routes, "_record_failed_attempt", lambda _key: None)
    monkeypatch.setattr(
        auth_routes.user_repository,
        "get_user_by_email",
        lambda email: UserModel(
            id="uid-123",
            firstName="Alice",
            surname="Meyer",
            email="alice@example.com",
            passwordHash=generate_password_hash("StrongPass9"),
            role="employee",
            language="en",
            disabled=False,
            createdAt=None,
        ),
    )

    with app.test_client() as client:
        for _ in range(5):
            response = client.post(
                "/auth/login",
                json={"email": "alice@example.com", "password": "wrong"},
            )
            assert response.status_code == 401
        blocked = client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "wrong"},
        )
    assert blocked.status_code == 429
    payload = blocked.get_json()
    assert payload["error"] == "Rate limit exceeded"
    assert payload["code"] == 429


def test_rate_limit_resets_after_window(monkeypatch):
    app = create_app("testing")
    auth_routes.FAILED_LOGIN_ATTEMPTS.clear()
    monkeypatch.setattr(auth_routes, "_too_many_failed_attempts", lambda _key: False)
    monkeypatch.setattr(auth_routes, "_record_failed_attempt", lambda _key: None)
    monkeypatch.setattr(
        auth_routes.user_repository,
        "get_user_by_email",
        lambda email: UserModel(
            id="uid-123",
            firstName="Alice",
            surname="Meyer",
            email="alice@example.com",
            passwordHash=generate_password_hash("StrongPass9"),
            role="employee",
            language="en",
            disabled=False,
            createdAt=None,
        ),
    )

    from middleware import rate_limiter as rate_limiter_module

    with app.test_client() as client:
        for _ in range(6):
            client.post("/auth/login", json={"email": "alice@example.com", "password": "wrong"})
        blocked = client.post("/auth/login", json={"email": "alice@example.com", "password": "wrong"})
        assert blocked.status_code == 429
        # Simulate end of limiter window by clearing in-memory storage.
        rate_limiter_module.limiter.reset()
        allowed_again = client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "wrong"},
        )
    assert allowed_again.status_code == 401


def test_audit_log_captures_ip_address(monkeypatch):
    captured = []

    class _ImmediateThread:
        def __init__(self, target=None, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(audit_repository, "_write_audit_log", lambda payload: captured.append(payload))
    monkeypatch.setattr(audit_repository.threading, "Thread", _ImmediateThread)
    fake_request = SimpleNamespace(
        remote_addr="203.0.113.1",
        headers={"User-Agent": "pytest-agent", "X-Forwarded-For": "198.51.100.12"},
        cookies={"session": "session-token"},
    )

    audit_repository.create_log(
        user_id="uid-1",
        action="user_login",
        details={"email": "alice@example.com"},
        request=fake_request,
    )
    assert captured
    assert captured[0]["ipAddress"] == "198.51.100.12"


def test_audit_log_does_not_block_main_request(monkeypatch):
    def _slow_write(_payload):
        time.sleep(0.2)

    monkeypatch.setattr(audit_repository, "_write_audit_log", _slow_write)
    started = time.perf_counter()
    audit_repository.create_log(user_id="uid-1", action="user_login", details={"email": "a@b.com"})
    elapsed = (time.perf_counter() - started) * 1000
    assert elapsed < 50
