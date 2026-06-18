from app import create_app
from routes import admin as admin_routes


def _session(client, role="admin"):
    with client.session_transaction() as flask_session:
        flask_session["uid"] = "admin-1" if role == "admin" else "user-1"
        flask_session["role"] = role
        flask_session["lang"] = "en"
        flask_session["name"] = "Admin User" if role == "admin" else "Employee User"


def _rows():
    return [
        {
            "document_id": "doc-1",
            "user_id": "user-1",
            "employee_name": "Alice Meyer",
            "email": "alice@example.com",
            "merchant": "Cafe Berlin",
            "date": "2026-06-10",
            "category": "Business Meal",
            "host": "Alice",
            "occasion": "Client lunch",
            "total_amount": 120.0,
            "currency": "EUR",
            "status": "pdf_generated",
            "created_at": "2026-06-11T10:00:00+00:00",
            "thumbnail": "",
            "pdf_url": "https://example.com/1.pdf",
        },
        {
            "document_id": "doc-2",
            "user_id": "user-2",
            "employee_name": "Bob Klein",
            "email": "bob@example.com",
            "merchant": "Hotel Alpha",
            "date": "2026-05-05",
            "category": "Hotel",
            "host": "Bob",
            "occasion": "Conference",
            "total_amount": 320.0,
            "currency": "EUR",
            "status": "processing",
            "created_at": "2026-05-06T10:00:00+00:00",
            "thumbnail": "",
            "pdf_url": "https://example.com/2.pdf",
        },
    ]


def test_admin_sees_all_users_data(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(admin_routes, "_load_admin_archive_entries", lambda: _rows())
    monkeypatch.setattr(admin_routes.user_repository, "get_all_users", lambda role: [])
    with app.test_client() as client:
        _session(client, role="admin")
        response = client.get("/admin/archive")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Alice Meyer" in body
    assert "Bob Klein" in body


def test_employee_cannot_access_admin_archive():
    app = create_app("testing")
    with app.test_client() as client:
        _session(client, role="employee")
        response = client.get("/admin/archive")
    assert response.status_code == 403


def test_amount_range_filter_works(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(admin_routes, "_load_admin_archive_entries", lambda: _rows())
    monkeypatch.setattr(admin_routes.user_repository, "get_all_users", lambda role: [])
    with app.test_client() as client:
        _session(client, role="admin")
        response = client.get("/admin/archive?amount_min=200&amount_max=400")
    body = response.get_data(as_text=True)
    assert "Hotel Alpha" in body
    assert "Cafe Berlin" not in body


def test_employee_filter_works(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(admin_routes, "_load_admin_archive_entries", lambda: _rows())
    monkeypatch.setattr(admin_routes.user_repository, "get_all_users", lambda role: [])
    with app.test_client() as client:
        _session(client, role="admin")
        response = client.get("/admin/archive?employee_id=user-1")
    body = response.get_data(as_text=True)
    assert "Alice Meyer" in body
    assert "Bob Klein" not in body


def test_csv_export_contains_all_columns(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(admin_routes, "_load_admin_archive_entries", lambda: _rows())
    with app.test_client() as client:
        _session(client, role="admin")
        response = client.get("/admin/archive/export")
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "document_id,employee_name,email,merchant,date,category,host,occasion,total_amount,currency,status,created_at" in text


def test_csv_export_respects_filters(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(admin_routes, "_load_admin_archive_entries", lambda: _rows())
    with app.test_client() as client:
        _session(client, role="admin")
        response = client.get("/admin/archive/export?merchant=hotel")
    text = response.get_data(as_text=True).lower()
    assert "hotel alpha" in text
    assert "cafe berlin" not in text
