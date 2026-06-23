from datetime import datetime, timezone

from app import create_app
from models.user import UserModel
from routes import admin as admin_routes
from routes import auth as auth_routes
from werkzeug.security import generate_password_hash


def _admin_session(client):
    with client.session_transaction() as flask_session:
        flask_session["uid"] = "admin-1"
        flask_session["role"] = "admin"
        flask_session["lang"] = "en"
        flask_session["name"] = "Admin"


def _employee_session(client):
    with client.session_transaction() as flask_session:
        flask_session["uid"] = "user-1"
        flask_session["role"] = "employee"
        flask_session["lang"] = "en"
        flask_session["name"] = "Employee"


def _user(uid: str, role: str, disabled: bool = False):
    return UserModel(
        id=uid,
        firstName="Alice" if uid == "user-1" else "Bob",
        surname="Meyer",
        email=f"{uid}@example.com",
        role=role,
        language="en",
        disabled=disabled,
        createdAt=datetime.now(timezone.utc),
    )


def test_promotion_requires_admin(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(admin_routes.user_repository, "get_user", lambda uid: _user(uid, "employee"))
    with app.test_client() as client:
        _employee_session(client)
        response = client.post("/admin/users/user-1/promote")
    assert response.status_code == 403


def test_admin_cannot_demote_self(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(admin_routes.user_repository, "get_user", lambda uid: _user(uid, "admin"))
    monkeypatch.setattr(admin_routes.user_repository, "get_all_users", lambda role: [_user("admin-1", "admin"), _user("admin-2", "admin")])
    with app.test_client() as client:
        _admin_session(client)
        response = client.post("/admin/users/admin-1/demote")
    assert response.status_code == 400
    assert "cannot demote themselves" in response.get_json()["message"].lower()


def test_last_admin_cannot_be_demoted(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(admin_routes.user_repository, "get_user", lambda uid: _user(uid, "admin"))
    monkeypatch.setattr(admin_routes.user_repository, "get_all_users", lambda role: [_user("admin-2", "admin")])
    with app.test_client() as client:
        _admin_session(client)
        response = client.post("/admin/users/admin-2/demote")
    assert response.status_code == 400
    assert "last admin" in response.get_json()["message"].lower()


def test_promotion_updates_database_role(monkeypatch):
    app = create_app("testing")
    calls = {}
    monkeypatch.setattr(admin_routes.user_repository, "get_user", lambda uid: _user(uid, "employee"))
    monkeypatch.setattr(admin_routes.user_repository, "update_user", lambda uid, data: calls.update({"uid": uid, "data": data}))
    monkeypatch.setattr(admin_routes.audit_repository, "create_log", lambda *args, **kwargs: None)
    with app.test_client() as client:
        _admin_session(client)
        response = client.post("/admin/users/user-1/promote")
    assert response.status_code == 200
    assert calls["uid"] == "user-1"
    assert calls["data"] == {"role": "admin"}


def test_promotion_creates_audit_log(monkeypatch):
    app = create_app("testing")
    events = []
    monkeypatch.setattr(admin_routes.user_repository, "get_user", lambda uid: _user(uid, "employee"))
    monkeypatch.setattr(admin_routes.user_repository, "update_user", lambda uid, data: None)
    monkeypatch.setattr(
        admin_routes.audit_repository,
        "create_log",
        lambda user_id, action, details=None, request=None: events.append(
            {"user_id": user_id, "action": action, "details": details or {}}
        ),
    )
    with app.test_client() as client:
        _admin_session(client)
        response = client.post("/admin/users/user-1/promote")
    assert response.status_code == 200
    assert events
    assert events[0]["action"] == "user_promoted_to_admin"
    assert events[0]["details"]["targetUser"] == "user-1"


def test_deactivated_user_cannot_login(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(
        auth_routes.user_repository,
        "get_user_by_email",
        lambda email: UserModel(
            id="uid-123",
            firstName="Alice",
            surname="Meyer",
            email="uid-123@example.com",
            passwordHash=generate_password_hash("StrongPass9"),
            role="employee",
            language="en",
            disabled=True,
            createdAt=datetime.now(timezone.utc),
        ),
    )
    with app.test_client() as client:
        response = client.post(
            "/auth/login",
            json={"email": "uid-123@example.com", "password": "StrongPass9"},
        )
    assert response.status_code == 403
    assert "disabled" in response.get_json()["errors"]["auth"].lower()
