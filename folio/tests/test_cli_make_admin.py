from __future__ import annotations

import app as app_module
from models.user import UserModel


def _user(role: str = "employee", disabled: bool = False) -> UserModel:
    return UserModel(
        id="user-1",
        firstName="Alice",
        surname="Meyer",
        email="alice@example.com",
        role=role,
        disabled=disabled,
    )


def test_make_admin_promotes_employee(monkeypatch):
    app = app_module.create_app("testing")
    calls = {"update": [], "audit": []}
    monkeypatch.setattr(
        app_module.user_repository,
        "get_user_by_email",
        lambda _email: _user(role="employee"),
    )
    monkeypatch.setattr(
        app_module.user_repository,
        "update_user",
        lambda uid, payload: calls["update"].append((uid, payload)),
    )
    monkeypatch.setattr(
        app_module.audit_repository,
        "create_log",
        lambda **kwargs: calls["audit"].append(kwargs),
    )
    runner = app.test_cli_runner()
    result = runner.invoke(args=["make-admin", "--email", "alice@example.com"])

    assert result.exit_code == 0
    assert calls["update"] == [("user-1", {"role": "admin"})]
    assert calls["audit"]
    assert "promoted to admin successfully" in result.output.lower()


def test_make_admin_dry_run_does_not_write(monkeypatch):
    app = app_module.create_app("testing")
    calls = {"update": 0}
    monkeypatch.setattr(
        app_module.user_repository,
        "get_user_by_email",
        lambda _email: _user(role="employee"),
    )
    monkeypatch.setattr(
        app_module.user_repository,
        "update_user",
        lambda _uid, _payload: calls.__setitem__("update", calls["update"] + 1),
    )
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=["make-admin", "--email", "alice@example.com", "--dry-run"]
    )

    assert result.exit_code == 0
    assert calls["update"] == 0
    assert "would be promoted to admin" in result.output


def test_make_admin_fails_for_unknown_user(monkeypatch):
    app = app_module.create_app("testing")
    monkeypatch.setattr(
        app_module.user_repository,
        "get_user_by_email",
        lambda _email: None,
    )
    runner = app.test_cli_runner()
    result = runner.invoke(args=["make-admin", "--email", "missing@example.com"])

    assert result.exit_code != 0
    assert "user not found" in result.output.lower()
