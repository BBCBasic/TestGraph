from app.api import development
from app.models.entities import (
    CapabilityCredential,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthRefreshToken,
    SchemaDefinition,
    User,
)
from scripts.reset_user_data import CONTENT_MODELS


def test_development_reset_is_hidden_by_default(client, monkeypatch):
    monkeypatch.setattr(development.settings, "enable_development_reset", False)
    assert client.get("/development/reset").status_code == 404
    assert client.post("/development/reset").status_code == 404
    assert "Reset database to basics" not in client.get("/").text


def test_development_reset_button_is_shown_on_home_when_enabled(client, monkeypatch):
    monkeypatch.setattr(development.settings, "enable_development_reset", True)

    home = client.get("/")
    assert home.status_code == 200
    assert 'action="/development/reset"' in home.text
    assert "Reset database to basics" in home.text
    assert "OAuth connections and API keys will be preserved" in home.text


def test_development_reset_page_has_single_confirmed_action(client, monkeypatch):
    monkeypatch.setattr(development.settings, "enable_development_reset", True)

    page = client.get("/development/reset")
    assert page.status_code == 200
    assert "Reset database to basics" in page.text
    assert "Permanently reset TasteGraph to basics?" in page.text
    assert "reset_token" not in page.text
    assert "confirmation" not in page.text
    assert page.headers["cache-control"] == "no-store"


def test_development_reset_preserves_authentication_and_schema_models():
    preserved_models = {
        User,
        SchemaDefinition,
        OAuthClient,
        OAuthAuthorizationCode,
        OAuthRefreshToken,
        CapabilityCredential,
    }
    assert preserved_models.isdisjoint(CONTENT_MODELS)


def test_development_reset_calls_shared_reset_service(client, monkeypatch):
    monkeypatch.setattr(development.settings, "enable_development_reset", True)
    calls = []

    def fake_reset():
        calls.append(True)
        return {"experiences": 2, "subjects": 1}

    monkeypatch.setattr(development, "reset_user_data", fake_reset)
    response = client.post("/development/reset")

    assert response.status_code == 200
    assert calls == [True]
    assert "Deleted 3 records" in response.text
    assert "OAuth connections and API credentials were preserved" in response.text
