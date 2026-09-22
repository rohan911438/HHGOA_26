"""Phase 2K - offline tests for GET /cases/... endpoints."""

from __future__ import annotations

from tests.api.conftest import KNOWN_TXN


def _create_case(client) -> dict:
    return client.post("/investigations", json={"transaction_id": KNOWN_TXN}).json()


class TestUnknownCase:
    def test_unknown_case_is_404(self, client):
        response = client.get("/cases/does-not-exist")
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "NOT_FOUND"
        assert "secret" not in str(body).lower()


class TestCaseRetrieval:
    def test_serialized_case_record_round_trips(self, client):
        created = _create_case(client)
        case_id = created["case_id"]

        response = client.get(f"/cases/{case_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["case_id"] == case_id
        assert body["transaction_id"] == KNOWN_TXN
        assert body["status"] in {"OPEN", "INVESTIGATING", "ACTION_RECOMMENDED", "PENDING_REVIEW", "CLOSED"}
        assert isinstance(body["evidence_ids"], list)
        # This is the real CaseRecord shape, not an ad hoc dict - no
        # dataset_risk_score/isFraud field exists on it anywhere.
        assert "dataset_risk_score" not in body
        assert "isFraud" not in body


class TestCaseEvidence:
    def test_full_evidence_detail_available_for_a_case_this_process_created(self, client):
        created = _create_case(client)

        response = client.get(f"/cases/{created['case_id']}/evidence")

        assert response.status_code == 200
        body = response.json()
        assert body["detail_available"] is True
        assert len(body["evidence"]) > 0
        item = body["evidence"][0]
        for field in ("evidence_id", "evidence_type", "status", "quality", "provenance"):
            assert field in item

    def test_evidence_for_unknown_case_is_404(self, client):
        response = client.get("/cases/does-not-exist/evidence")
        assert response.status_code == 404


class TestCaseHistory:
    def test_history_includes_case_created_and_is_chronological(self, client):
        created = _create_case(client)

        response = client.get(f"/cases/{created['case_id']}/history")

        assert response.status_code == 200
        body = response.json()
        event_types = [e["event_type"] for e in body["events"]]
        assert "CASE_CREATED" in event_types
        timestamps = [e["occurred_at"] for e in body["events"]]
        assert timestamps == sorted(timestamps)
        assert "note" in body


class TestSimilarCases:
    def test_uses_case_memory_and_returns_no_self_match(self, client):
        first = _create_case(client)
        second = _create_case(client)

        response = client.get(f"/cases/{first['case_id']}/similar")

        assert response.status_code == 200
        body = response.json()
        result_ids = [c["case_id"] for c in body["similar_cases"]]
        assert first["case_id"] not in result_ids
        assert second["case_id"] in result_ids
        assert body["similar_cases"][0]["similarity_score"] >= 0.0
        assert "SYNTHETIC" in body["note"] or "PROJECT DEVELOPMENT HEURISTIC" in body["note"]

    def test_limit_query_param_is_honored(self, client):
        first = _create_case(client)
        for _ in range(3):
            _create_case(client)

        response = client.get(f"/cases/{first['case_id']}/similar", params={"limit": 1})
        assert response.status_code == 200
        assert len(response.json()["similar_cases"]) <= 1


class TestCaseContext:
    def test_context_available_for_a_case_this_process_created(self, client):
        created = _create_case(client)

        response = client.get(f"/cases/{created['case_id']}/context")

        assert response.status_code == 200
        body = response.json()
        assert body["available"] is True
        assert body["context"]["transaction_id"] == KNOWN_TXN
        assert body["context"]["case_id"] == created["case_id"]
        assert "current_evidence" in body["context"]
        assert "historical_cases" in body["context"]

    def test_context_for_unknown_case_is_404(self, client):
        response = client.get("/cases/does-not-exist/context")
        assert response.status_code == 404
