"""Sanity check: the app starts and /health responds correctly."""


def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_health_returns_ok_status(client):
    response = client.get("/health")
    body = response.json()
    assert body["status"] == "ok"
    assert "env" in body
