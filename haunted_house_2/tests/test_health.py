def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_pages_render(client):
    for path in ("/", "/host", "/player"):
        response = client.get(path)
        assert response.status_code == 200
        assert "Escape the Haunted House" in response.text


def test_static_assets_served(client):
    assert client.get("/static/js/common.js").status_code == 200
