def test_deliberations_page_explains_lifecycle_and_human_authority(client):
    response = client.get("/deliberations")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "The TestGraph deliberation lifecycle" in response.text
    assert "proposal · critique · counterproposal" in response.text
    assert "Only the user may" in response.text
    assert "147 tests passed · 0 failed" in response.text
    assert 'role="img"' in response.text
    assert "diagram-desc" in response.text


def test_landing_page_links_to_deliberations(client):
    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/deliberations"' in response.text
    assert "See how deliberations work" in response.text
