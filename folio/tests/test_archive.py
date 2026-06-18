from app import create_app
from routes import archive as archive_routes


def _auth_session(client):
    with client.session_transaction() as flask_session:
        flask_session["uid"] = "user-1"
        flask_session["role"] = "employee"
        flask_session["lang"] = "en"
        flask_session["name"] = "Alice"


def _records():
    return [
        {
            "id": "doc-1",
            "userId": "user-1",
            "merchant": "Cafe Berlin",
            "date": "2026-06-10",
            "category": "Business Meal",
            "occasion": "Client lunch",
            "totalAmount": 120.0,
            "status": "pdf_generated",
            "thumbnailUrl": "",
            "pdfUrl": "https://example.com/1.pdf",
            "createdAt": "2026-06-11T10:00:00+00:00",
        },
        {
            "id": "doc-2",
            "userId": "user-1",
            "merchant": "Hotel Alpha",
            "date": "2026-05-05",
            "category": "Hotel",
            "occasion": "Conference",
            "totalAmount": 320.0,
            "status": "processing",
            "thumbnailUrl": "",
            "pdfUrl": "https://example.com/2.pdf",
            "createdAt": "2026-06-01T10:00:00+00:00",
        },
        {
            "id": "doc-3",
            "userId": "user-2",
            "merchant": "Travel Co",
            "date": "2026-04-01",
            "category": "Travel",
            "occasion": "Trip",
            "totalAmount": 50.0,
            "status": "error",
            "thumbnailUrl": "",
            "pdfUrl": "https://example.com/3.pdf",
            "createdAt": "2026-04-02T10:00:00+00:00",
        },
    ]


def test_archive_only_returns_current_user_data(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(
        archive_routes,
        "_fetch_archive_page",
        lambda user_id, cursor=None, page_size=200: (_records(), None, 3),
    )
    with app.test_client() as client:
        _auth_session(client)
        response = client.get("/archive/data")
    assert response.status_code == 200
    payload = response.get_json()
    assert all(item["userId"] == "user-1" for item in payload["results"])


def test_filter_by_merchant_works(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(
        archive_routes,
        "_fetch_archive_page",
        lambda user_id, cursor=None, page_size=200: (_records(), None, 3),
    )
    with app.test_client() as client:
        _auth_session(client)
        response = client.get("/archive/data?merchant=cafe")
    assert response.status_code == 200
    results = response.get_json()["results"]
    assert len(results) == 1
    assert results[0]["merchant"] == "Cafe Berlin"


def test_filter_by_date_range_works(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(
        archive_routes,
        "_fetch_archive_page",
        lambda user_id, cursor=None, page_size=200: (_records(), None, 3),
    )
    with app.test_client() as client:
        _auth_session(client)
        response = client.get("/archive/data?date_from=2026-06-01&date_to=2026-06-30")
    results = response.get_json()["results"]
    assert len(results) == 1
    assert results[0]["id"] == "doc-1"


def test_filter_by_category_works(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(
        archive_routes,
        "_fetch_archive_page",
        lambda user_id, cursor=None, page_size=200: (_records(), None, 3),
    )
    with app.test_client() as client:
        _auth_session(client)
        response = client.get("/archive/data?category=Hotel")
    results = response.get_json()["results"]
    assert len(results) == 1
    assert results[0]["id"] == "doc-2"


def test_sort_by_amount_works(monkeypatch):
    app = create_app("testing")
    monkeypatch.setattr(
        archive_routes,
        "_fetch_archive_page",
        lambda user_id, cursor=None, page_size=200: (_records(), None, 3),
    )
    with app.test_client() as client:
        _auth_session(client)
        response = client.get("/archive/data?sort=amount_high")
    results = response.get_json()["results"]
    assert [item["id"] for item in results][:2] == ["doc-2", "doc-1"]


def test_pagination_returns_correct_page(monkeypatch):
    app = create_app("testing")

    def _fake_fetch(user_id, cursor=None, page_size=200):
        if cursor == "cursor-1":
            return (
                [
                    {
                        "id": "doc-2",
                        "userId": "user-1",
                        "merchant": "Hotel Alpha",
                        "date": "2026-05-05",
                        "category": "Hotel",
                        "occasion": "Conference",
                        "totalAmount": 320.0,
                        "status": "processing",
                        "thumbnailUrl": "",
                        "pdfUrl": "https://example.com/2.pdf",
                        "createdAt": "2026-06-01T10:00:00+00:00",
                    }
                ],
                None,
                2,
            )
        return (
            [
                {
                    "id": "doc-1",
                    "userId": "user-1",
                    "merchant": "Cafe Berlin",
                    "date": "2026-06-10",
                    "category": "Business Meal",
                    "occasion": "Client lunch",
                    "totalAmount": 120.0,
                    "status": "pdf_generated",
                    "thumbnailUrl": "",
                    "pdfUrl": "https://example.com/1.pdf",
                    "createdAt": "2026-06-11T10:00:00+00:00",
                }
            ],
            "cursor-1",
            2,
        )

    monkeypatch.setattr(archive_routes, "_fetch_archive_page", _fake_fetch)
    with app.test_client() as client:
        _auth_session(client)
        first = client.get("/archive/data").get_json()
        second = client.get("/archive/data?cursor=cursor-1").get_json()
    assert first["nextCursor"] == "cursor-1"
    assert first["results"][0]["id"] == "doc-1"
    assert second["results"][0]["id"] == "doc-2"
