"""Phase 2K - offline tests for /health."""

from __future__ import annotations


class TestHealth:
    def test_root_is_200_and_points_to_docs(self, client):
        response = client.get("/")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["links"]["docs"] == "/docs"

    def test_health_is_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "hhgoa-fraud-agent"}

    def test_health_dependencies_distinguishes_application_from_dependency_health(self, client):
        response = client.get("/health/dependencies")
        assert response.status_code == 200
        body = response.json()
        names = {d["name"] for d in body["dependencies"]}
        assert names == {"tigergraph", "llm"}
        for dep in body["dependencies"]:
            assert dep["checked"] is True  # never a fabricated/untested status


class TestCors:
    def test_frontend_origin_preflight_succeeds(self, client):
        # A real browser (unlike this TestClient's usual same-process
        # calls) enforces CORS on cross-origin fetches - the frontend runs
        # on a different port in local dev. Without CORSMiddleware this
        # preflight 405s and every frontend request silently fails before
        # ever reaching a route.
        response = client.options(
            "/health/dependencies",
            headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"

    def test_unlisted_origin_is_not_granted_cors(self, client):
        response = client.options(
            "/health/dependencies",
            headers={"Origin": "http://evil.example.com", "Access-Control-Request-Method": "GET"},
        )
        assert "access-control-allow-origin" not in response.headers
