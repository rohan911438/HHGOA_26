"""Phase 2K - offline tests for /health."""

from __future__ import annotations


class TestHealth:
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
