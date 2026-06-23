from app import create_app
from routes import auth as auth_routes
from utils.helpers import translate


def test_english_translation_loads():
    assert translate("form.title", "en") == "Hospitality Expense Form"


def test_german_translation_loads():
    assert translate("form.title", "de") == "Bewirtungskostenformular"


def test_missing_key_returns_key_string():
    assert translate("form.notARealKey", "en") == "form.notARealKey"


def test_language_session_persists():
    app = create_app("testing")
    with app.test_client() as client:
        response = client.post("/auth/set-language", json={"lang": "de"})
        assert response.status_code == 200

        with client.session_transaction() as flask_session:
            assert flask_session["lang"] == "de"

        follow_up = client.get("/")
        assert follow_up.status_code == 200


def test_german_hospitality_terms_present():
    assert translate("form.date", "de") == "Tag der Bewirtung"
    assert translate("form.location", "de") == "Ort der Bewirtung"
    assert translate("form.host", "de") == "Bewirtende Person"
    assert translate("form.guests", "de") == "Bewirtete Personen"
    assert translate("form.occasion", "de") == "Anlass der Bewirtung"


def test_authenticated_language_saved_to_database(monkeypatch):
    saved = {}

    app = create_app("testing")
    monkeypatch.setattr(
        auth_routes.user_repository,
        "update_user",
        lambda user_id, payload: saved.update({"user_id": user_id, "payload": payload}),
    )
    with app.test_client() as client:
        with client.session_transaction() as flask_session:
            flask_session["uid"] = "user-123"
        response = client.post(
            "/auth/set-language",
            json={"lang": "de"},
        )

    assert response.status_code == 200
    assert response.get_json()["savedToProfile"] is True
    assert saved["user_id"] == "user-123"
    assert saved["payload"] == {"language": "de"}
