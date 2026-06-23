from datetime import datetime, timezone

from app import create_app
from models.user import UserModel
from routes import auth as auth_routes
from werkzeug.security import generate_password_hash


def _mock_valid_login(monkeypatch, role="employee"):
    monkeypatch.setattr(
        auth_routes.user_repository,
        "get_user_by_email",
        lambda email: UserModel(
            id="uid-123",
            firstName="Alice",
            surname="Meyer",
            email="alice@example.com",
            passwordHash=generate_password_hash("StrongPass9"),
            role=role,
            language="en",
            createdAt=datetime.now(timezone.utc),
        ),
    )


def test_valid_login_creates_session(monkeypatch):
    app = create_app("testing")
    _mock_valid_login(monkeypatch, role="employee")

    with app.test_client() as client:
        response = client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "StrongPass9"},
        )
        assert response.status_code == 200
        with client.session_transaction() as flask_session:
            assert flask_session["uid"] == "uid-123"
            assert flask_session["email"] == "alice@example.com"
            assert flask_session["name"] == "Alice"


def test_invalid_password_returns_error(monkeypatch):
    app = create_app("testing")
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
            createdAt=datetime.now(timezone.utc),
        ),
    )

    with app.test_client() as client:
        response = client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "wrong-pass"},
        )

    assert response.status_code == 401
    assert "auth" in response.get_json()["errors"]


def test_session_contains_correct_role(monkeypatch):
    app = create_app("testing")
    _mock_valid_login(monkeypatch, role="admin")

    with app.test_client() as client:
        response = client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "StrongPass9"},
        )
        assert response.status_code == 200
        with client.session_transaction() as flask_session:
            assert flask_session["role"] == "admin"


def test_logout_clears_session(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(auth_routes.audit_repository, "create_log", lambda *args, **kwargs: None)
    with app.test_client() as client:
        with client.session_transaction() as flask_session:
            flask_session["uid"] = "uid-123"
            flask_session["email"] = "alice@example.com"
            flask_session["role"] = "employee"
            flask_session["lang"] = "en"
            flask_session["name"] = "Alice"
        response = client.get("/auth/logout")
        assert response.status_code == 302
        with client.session_transaction() as flask_session:
            assert "uid" not in flask_session
            assert "email" not in flask_session
            assert "role" not in flask_session


def test_protected_route_redirects_unauthenticated():
    app = create_app("testing")
    with app.test_client() as client:
        response = client.get("/receipts/status")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_admin_route_rejects_employee():
    app = create_app("testing")
    with app.test_client() as client:
        with client.session_transaction() as flask_session:
            flask_session["uid"] = "uid-123"
            flask_session["role"] = "employee"
            flask_session["lang"] = "en"
        response = client.get("/admin/status")
    assert response.status_code == 403


def test_rate_limiting_after_5_failures(monkeypatch):
    auth_routes.FAILED_LOGIN_ATTEMPTS.clear()
    app = create_app("testing")
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
            createdAt=datetime.now(timezone.utc),
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


def test_password_reset_sends_email(monkeypatch):
    calls = {"email": 0, "audit": 0}

    monkeypatch.setattr(
        auth_routes,
        "_send_password_reset_email",
        lambda email, reset_link: calls.__setitem__("email", calls["email"] + 1),
    )
    monkeypatch.setattr(
        auth_routes.audit_repository,
        "create_log",
        lambda *args, **kwargs: calls.__setitem__("audit", calls["audit"] + 1),
    )
    app = create_app("testing")
    with app.test_client() as client:
        response = client.post("/auth/forgot-password", json={"email": "alice@example.com"})

    assert response.status_code == 200
    assert calls["email"] == 1
    assert calls["audit"] == 1


def test_account_settings_updates_database(monkeypatch):
    updated = {}
    app = create_app("testing")
    monkeypatch.setattr(
        auth_routes.user_repository,
        "get_user",
        lambda uid: UserModel(
            id=uid,
            firstName="Alice",
            surname="Meyer",
            email="alice@example.com",
            role="employee",
            language="en",
            createdAt=datetime.now(timezone.utc),
        ),
    )
    monkeypatch.setattr(
        auth_routes.user_repository,
        "update_user",
        lambda uid, data: updated.update({"uid": uid, "data": data}),
    )
    with app.test_client() as client:
        with client.session_transaction() as flask_session:
            flask_session["uid"] = "uid-123"
            flask_session["role"] = "employee"
            flask_session["lang"] = "en"
            flask_session["name"] = "Alice"
        response = client.post(
            "/auth/settings",
            json={"action": "profile", "firstName": "Alicia", "surname": "Meyer"},
        )
        assert response.status_code == 200
        with client.session_transaction() as flask_session:
            assert flask_session["name"] == "Alicia"

    assert updated["uid"] == "uid-123"
    assert updated["data"] == {"firstName": "Alicia", "surname": "Meyer"}


def test_language_change_persists_to_database(monkeypatch):
    writes = {"saved": False}

    app = create_app("testing")
    monkeypatch.setattr(
        auth_routes.user_repository,
        "update_user",
        lambda uid, data: writes.__setitem__("saved", uid == "uid-123" and data == {"language": "de"}),
    )

    with app.test_client() as client:
        with client.session_transaction() as flask_session:
            flask_session["uid"] = "uid-123"
            flask_session["role"] = "employee"
            flask_session["lang"] = "en"
        response = client.post("/auth/set-language", json={"lang": "de"})

    assert response.status_code == 200
    assert response.get_json()["savedToProfile"] is True
    assert writes["saved"] is True


def test_email_cannot_be_changed(monkeypatch):
    captured = {}
    app = create_app("testing")
    monkeypatch.setattr(
        auth_routes.user_repository,
        "get_user",
        lambda uid: UserModel(
            id=uid,
            firstName="Alice",
            surname="Meyer",
            email="alice@example.com",
            role="employee",
            language="en",
            createdAt=datetime.now(timezone.utc),
        ),
    )
    monkeypatch.setattr(
        auth_routes.user_repository,
        "update_user",
        lambda uid, data: captured.update({"uid": uid, "data": data}),
    )
    with app.test_client() as client:
        with client.session_transaction() as flask_session:
            flask_session["uid"] = "uid-123"
            flask_session["role"] = "employee"
            flask_session["lang"] = "en"
        response = client.post(
            "/auth/settings",
            json={
                "action": "profile",
                "firstName": "Alice",
                "surname": "Meyer",
                "email": "hacker@example.com",
            },
        )

    assert response.status_code == 200
    assert "email" not in captured["data"]


def test_role_not_shown_in_edit_form(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(
        auth_routes.user_repository,
        "get_user",
        lambda uid: UserModel(
            id=uid,
            firstName="Alice",
            surname="Meyer",
            email="alice@example.com",
            role="employee",
            language="en",
            createdAt=datetime.now(timezone.utc),
        ),
    )
    with app.test_client() as client:
        with client.session_transaction() as flask_session:
            flask_session["uid"] = "uid-123"
            flask_session["role"] = "employee"
            flask_session["lang"] = "en"
        response = client.get("/auth/settings")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'name="role"' not in body
    assert 'name="email"' not in body
