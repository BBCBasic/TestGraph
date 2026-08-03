def test_review_browser_is_linked_and_uses_public_api(client):
    home = client.get("/")
    assert home.status_code == 200
    assert 'href="/reviews"' in home.text

    page = client.get("/reviews")
    assert page.status_code == 200
    assert "Review browser" in page.text
    assert "fetchAll('/api/v1/experiences')" in page.text
    assert "fetchAll('/api/v1/subjects')" in page.text
    assert "Raw JSON" in page.text
    assert "offset+=100" in page.text
