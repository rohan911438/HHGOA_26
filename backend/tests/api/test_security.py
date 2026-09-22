"""Phase 2K - security checks (2K.6 / test spec L).

No secret, raw GSQL, or MCP-internal string ever appears in a response,
across the widest response surfaces the API exposes: an investigation
result (which nests Evidence.Provenance, InvestigationContext, and
PolicyDecision), a case record, and the dependency health check (which
deliberately touches TigerGraph/LLM configuration).
"""

from __future__ import annotations

from app.config import get_settings
from tests.api.conftest import KNOWN_TXN

# Structural markers of a leak (raw GSQL, an MCP internal, a PEM key) -
# never a config *variable name* like "openai_api_key", which is fine to
# mention in a diagnostic message (it tells an operator what to set).
_FORBIDDEN_SUBSTRINGS = (
    "BEGIN PRIVATE KEY",
    "gsql>",
    "install query",
    "tigergraph-mcp",
)


def _forbidden_secret_values() -> tuple[str, ...]:
    settings = get_settings()
    values = []
    for secret in (settings.tg_secret, settings.tg_password, settings.tg_api_token, settings.tg_jwt_token, settings.openai_api_key):
        real = secret.get_secret_value()
        if real:
            values.append(real)
    return tuple(values)


def _assert_no_forbidden_content(text: str) -> None:
    lowered = text.lower()
    for needle in _FORBIDDEN_SUBSTRINGS:
        assert needle.lower() not in lowered, f"response leaked forbidden content: {needle}"
    for secret_value in _forbidden_secret_values():
        assert secret_value not in text, "response leaked a real configured secret value"


class TestNoSecretLeakage:
    def test_investigation_response_is_clean(self, client):
        response = client.post("/investigations", json={"transaction_id": KNOWN_TXN})
        _assert_no_forbidden_content(response.text)

    def test_case_response_is_clean(self, client):
        created = client.post("/investigations", json={"transaction_id": KNOWN_TXN}).json()
        response = client.get(f"/cases/{created['case_id']}")
        _assert_no_forbidden_content(response.text)

    def test_health_dependencies_never_returns_a_credential_value(self, client):
        response = client.get("/health/dependencies")
        _assert_no_forbidden_content(response.text)
        body = response.json()
        for dep in body["dependencies"]:
            assert set(dep.keys()) == {"name", "checked", "healthy", "detail", "latency_ms"}

    def test_unexpected_internal_error_never_leaks_its_message(self, app, monkeypatch):
        from fastapi.testclient import TestClient

        from app.api.dependencies import get_case_manager

        def broken_case_manager():
            raise RuntimeError("SECRET_DEBUG_DETAIL should never reach the client")

        app.dependency_overrides[get_case_manager] = broken_case_manager

        # Starlette's ServerErrorMiddleware re-raises after sending the
        # response (by design, so a real ASGI server's own logging still
        # sees it) - raise_server_exceptions=False makes the test client
        # behave like a real HTTP client and just inspect what was sent,
        # which is what this test is actually verifying.
        no_raise_client = TestClient(app, raise_server_exceptions=False)
        response = no_raise_client.get("/cases/whatever")

        assert response.status_code == 500
        body = response.json()
        assert body["error"]["code"] == "INTERNAL_ERROR"
        assert "SECRET_DEBUG_DETAIL" not in response.text


class TestNoArbitraryGraphAccess:
    def test_no_route_accepts_a_raw_query_parameter(self, client):
        openapi = client.get("/openapi.json").json()
        for methods in openapi["paths"].values():
            for operation in methods.values():
                for param in operation.get("parameters", []):
                    name = param["name"].lower()
                    assert "gsql" not in name
                    assert "query" != name  # only named, typed params like limit/min_similarity exist
                    assert "cypher" not in name


def test_settings_default_to_no_real_credentials_leaking_into_the_app_object():
    # Sanity check on the config layer this API depends on - SecretStr
    # fields never render their value via str()/repr().
    settings = get_settings()
    assert "SecretStr" in repr(settings.tg_secret) or str(settings.tg_secret) == "**********"
