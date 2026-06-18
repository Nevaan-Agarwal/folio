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


def test_authenticated_language_saved_to_firestore(monkeypatch):
    saved = {}

    class _FakeDocRef:
        def set(self, payload, merge=False):
            saved["payload"] = payload
            saved["merge"] = merge

    class _FakeCollection:
        def document(self, user_id):
            saved["user_id"] = user_id
            return _FakeDocRef()

    class _FakeDB:
        def collection(self, _name):
            return _FakeCollection()

    class _FakeAuth:
        @staticmethod
        def verify_id_token(_token):
            return {"uid": "user-123"}

    app = create_app("testing")
    monkeypatch.setattr(auth_routes.firebase_config, "db", _FakeDB())
    monkeypatch.setattr(auth_routes.firebase_config, "firebase_auth", _FakeAuth())
    with app.test_client() as client:
        response = client.post(
            "/auth/set-language",
            json={"lang": "de"},
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    assert response.get_json()["savedToProfile"] is True
    assert saved["user_id"] == "user-123"
    assert saved["payload"] == {"language": "de"}
    assert saved["merge"] is True
