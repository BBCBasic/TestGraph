from app.api import development


def test_development_reset_is_hidden_by_default(client, monkeypatch):
    monkeypatch.setattr(development.settings, "enable_development_reset", False)
    assert client.get("/development/reset").status_code == 404
    assert client.post("/development/reset").status_code == 404


def test_development_reset_page_has_single_confirmed_action(client, monkeypatch):
    monkeypatch.setattr(development.settings, "enable_development_reset", True)

    page = client.get("/development/reset")
    assert page.status_code == 200
    assert "Empty user data" in page.text
    assert "Permanently empty all TasteGraph user data?" in page.text
    assert "reset_token" not in page.text
    assert "confirmation" not in page.text
    assert page.headers["cache-control"] == "no-store"


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
