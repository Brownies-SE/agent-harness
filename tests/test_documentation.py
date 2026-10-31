"""Offline docs remain public shells; data and schema retain bearer authentication."""

import pytest
from fastapi.testclient import TestClient

from agent_harness.api import create_app
from agent_harness.routes.documentation import install_documentation

TOKEN = "docs-test-token-with-at-least-32-characters"  # noqa: S105 -- synthetic token
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def client():
    app = create_app(object(), token=TOKEN, model_name="test")
    if not any(getattr(route, "path", None) == "/docs" for route in app.routes):
        install_documentation(app)
    with TestClient(app, base_url="http://127.0.0.1") as browser:
        yield browser


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_documentation_shell_is_public_without_reflecting_token(client, path):
    response = client.get(path, headers=AUTH)
    assert response.status_code == 200
    assert TOKEN not in response.text
    assert client.get(path).status_code == 200


def test_file_upload_has_a_larger_bounded_body_without_weakening_other_routes(client):
    payload = '{"content":"' + "a" * 65536 + '"}'
    headers = {**AUTH, "Content-Type": "application/json"}
    assert client.post("/api/files", content=payload, headers=headers).status_code != 413
    for path in ("/api/files/", "/api/files/anything", "/api/conversations", "/docs"):
        assert client.post(path, content=payload, headers=headers).status_code == 413
    assert client.put("/api/files", content=payload, headers=headers).status_code == 413
    assert (
        client.post("/api/files", content="a" * (8 * 1024 * 1024 + 1), headers=AUTH).status_code
        == 413
    )


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/docs-assets/docs.js"])
def test_documentation_rejects_host_rebinding_before_public_exemption(client, path):
    assert client.get(path, headers={"Host": "attacker.example"}).status_code == 400


@pytest.mark.parametrize(
    "path",
    [
        "/openapi.json",
        "/api/health",
        "/docs/anything",
        "/docs-assets/unknown.js",
        "/docs-assets/%2e%2e/openapi.json",
        "/docs-assets/docs.js/extra",
        "/redoc/",
    ],
)
def test_documentation_exemptions_do_not_bypass_authentication(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, params={"token": TOKEN}).status_code == 401
    assert client.get(path, headers={"Cookie": "token=" + TOKEN}).status_code == 401


def test_documentation_exemptions_only_allow_get(client):
    for path in ("/docs", "/redoc", "/docs-assets/docs.js"):
        assert client.post(path, json={}).status_code == 401
        assert client.head(path).status_code == 401


def test_openapi_uses_bearer_security_and_is_protected(client):
    assert client.get("/openapi.json").status_code == 401
    response = client.get("/openapi.json", headers=AUTH)
    assert response.status_code == 200
    schema = response.json()
    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }
    for methods in schema["paths"].values():
        for operation in methods.values():
            assert operation["security"] == [{"HTTPBearer": []}]


def test_local_docs_assets_have_correct_types_and_security_policy(client):
    for path, mime in [
        ("docs.js", "application/javascript"),
        ("docs.css", "text/css"),
        ("swagger-ui-bundle.js", "application/javascript"),
        ("swagger-ui.css", "text/css"),
        ("redoc.standalone.js", "application/javascript"),
    ]:
        response = client.get("/docs-assets/" + path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(mime)
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
    for path in ("/docs", "/redoc"):
        response = client.get(path)
        assert 'type="password"' in response.text
        assert "https://" not in response.text
        assert "connect-src 'self'" in response.headers["content-security-policy"]


def test_upload_still_requires_auth_and_strict_json(client):
    assert client.post("/api/files", json={}).status_code == 401
    assert client.post("/api/files", headers=AUTH, content="plain").status_code == 415
    headers = {**AUTH, "Content-Type": "application/json"}
    for body in ('{"x":1,"x":2}', '{"x":NaN}'):
        assert client.post("/api/files", headers=headers, content=body).status_code == 422
