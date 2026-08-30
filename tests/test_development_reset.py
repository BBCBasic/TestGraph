from app.api import development
from app.models.deliberation import Deliberation, DeliberationContribution
from app.models.entities import (
    CapabilityCredential,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthRefreshToken,
    SchemaDefinition,
    User,
)
from app.models.v2 import SubjectRelationship, V2Subject
from scripts.reset_user_data import CONTENT_MODELS


def test_development_reset_is_hidden_by_default(client, monkeypatch):
    monkeypatch.setattr(development.settings, "enable_development_reset", False)
    assert client.get("/development/reset").status_code == 404
    assert client.post("/development/reset").status_code == 404
    assert client.post("/development/tg-ai-connectivity").status_code == 404
    assert "Reset database to basics" not in client.get("/").text


def test_development_reset_button_is_shown_on_home_when_enabled(client, monkeypatch):
    monkeypatch.setattr(development.settings, "enable_development_reset", True)

    home = client.get("/")
    assert home.status_code == 200
    assert 'action="/development/reset"' in home.text
    assert "Reset database to basics" in home.text
    assert "OAuth connections and API keys will be preserved" in home.text


def test_development_reset_page_has_connectivity_test_action(client, monkeypatch):
    monkeypatch.setattr(development.settings, "enable_development_reset", True)

    page = client.get("/development/reset")
    assert page.status_code == 200
    assert "Reset database to basics" in page.text
    assert "Permanently reset TestGraph to basics?" in page.text
    assert 'action="/development/tg-ai-connectivity"' in page.text
    assert "Test TG-AI connectivity" in page.text
    assert "reset_token" not in page.text
    assert "confirmation" not in page.text
    assert page.headers["cache-control"] == "no-store"


def test_development_connectivity_action_calls_non_mutating_probe(client, monkeypatch):
    monkeypatch.setattr(development.settings, "enable_development_reset", True)
    calls = []

    def fake_probe():
        calls.append(True)
        return {
            "ok": True,
            "enabled": True,
            "api_key_present": True,
            "model": "gpt-test",
            "response_id": "resp_test",
            "response_text": "TG_AI_OK",
        }

    monkeypatch.setattr(development, "check_resolver_connectivity", fake_probe)
    response = client.post("/development/tg-ai-connectivity")

    assert response.status_code == 200
    assert calls == [True]
    assert "TG-AI connectivity OK" in response.text
    assert "resp_test" in response.text
    assert "TG_AI_OK" in response.text


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


def test_development_reset_deletes_subject_relationships_before_subjects():
    assert SubjectRelationship in CONTENT_MODELS
    assert CONTENT_MODELS.index(SubjectRelationship) < CONTENT_MODELS.index(V2Subject)


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


def test_development_reset_deletes_deliberation_children_before_parents():
    assert DeliberationContribution in CONTENT_MODELS
    assert Deliberation in CONTENT_MODELS
    assert CONTENT_MODELS.index(DeliberationContribution) < CONTENT_MODELS.index(Deliberation)
