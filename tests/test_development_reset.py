from app.api import development


def test_development_reset_is_hidden_by_default(client, monkeypatch):
    monkeypatch.setattr(development.settings, "enable_development_reset", False)
    assert client.get("/development/reset").status_code == 404


def test_development_reset_requires_confirmation_and_token(client, monkeypatch):
    monkeypatch.setattr(development.settings, "enable_development_reset", True)
    monkeypatch.setattr(
        development.settings,
        "development_reset_token",
        "test-reset-token-long-enough",
    )

    page = client.get("/development/reset")
    assert page.status_code == 200
    assert "EMPTY USER DATA" in page.text
    assert page.headers["cache-control"] == "no-store"

    wrong_confirmation = client.post(
        "/development/reset",
        data={
            "confirmation": "EMPTY",
            "reset_token": "test-reset-token-long-enough",
        },
    )
    assert wrong_confirmation.status_code == 200
    assert "exactly before resetting" in wrong_confirmation.text

    wrong_token = client.post(
        "/development/reset",
        data={
            "confirmation": "EMPTY USER DATA",
            "reset_token": "wrong-token",
        },
    )
    assert wrong_token.status_code == 403


def test_development_reset_calls_shared_reset_service(client, monkeypatch):
    monkeypatch.setattr(development.settings, "enable_development_reset", True)
    monkeypatch.setattr(
        development.settings,
        "development_reset_token",
        "test-reset-token-long-enough",
    )
    calls = []

    def fake_reset():
        calls.append(True)
        return {"experiences": 2, "subjects": 1}

    monkeypatch.setattr(development, "reset_user_data", fake_reset)
    response = client.post(
        "/development/reset",
        data={
            "confirmation": "EMPTY USER DATA",
            "reset_token": "test-reset-token-long-enough",
        },
    )

    assert response.status_code == 200
    assert calls == [True]
    assert "Deleted 3 records" in response.text
