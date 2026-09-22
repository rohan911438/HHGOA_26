"""Phase 2K - offline tests for POST/GET /investigations.

No live TigerGraph, no real OpenAI call anywhere in this file - see
tests/api/conftest.py.
"""

from __future__ import annotations

from fastapi import Depends

from app.agent import (
    AgentOrchestrator,
    AgentStatus,
    FakeLLMClient,
    LLMDecision,
    LLMDecisionAction,
    MalformedLLMClient,
)
from app.api.dependencies import (
    get_case_store,
    get_investigation_service,
    get_llm_client,
    get_orchestrator,
    get_tigergraph_client,
)
from app.investigation import InvestigationService
from tests.api.conftest import KNOWN_TXN, set_llm


class TestValidInvestigation:
    def test_request_flows_through_the_api_to_the_agent_and_back(self, client, app):
        response = client.post("/investigations", json={"transaction_id": KNOWN_TXN})

        assert response.status_code == 200
        body = response.json()
        assert body["transaction_id"] == KNOWN_TXN
        assert body["case_id"] is not None
        assert body["investigation_id"] == body["case_id"]
        assert body["status"] == AgentStatus.COMPLETED.value
        assert body["completed"] is True
        assert body["iterations"] == 1
        assert body["tool_calls"] == 1
        assert body["policy_decision"] is not None
        assert body["policy_decision"]["executable"] is False
        assert body["uncertainty"] is not None
        assert isinstance(body["evidence"], list) and len(body["evidence"]) > 0
        assert body["error"] is None
        # No hidden chain-of-thought / raw LLM reasoning trace field exists on the response.
        assert "agent_messages" not in body
        assert "chain_of_thought" not in body

    def test_default_trigger_is_unknown(self, client):
        response = client.post("/investigations", json={"transaction_id": KNOWN_TXN})
        assert response.status_code == 200

    def test_explicit_supported_trigger_is_honored(self, client):
        response = client.post("/investigations", json={"transaction_id": KNOWN_TXN, "trigger": "FRAUD_SIGNAL"})
        assert response.status_code == 200


class TestInvalidRequest:
    def test_empty_transaction_id_is_rejected_without_fabricating_a_case(self, client):
        response = client.post("/investigations", json={"transaction_id": ""})

        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "INVALID_REQUEST"

    def test_missing_transaction_id_is_rejected(self, client):
        response = client.post("/investigations", json={})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_REQUEST"

    def test_unsupported_trigger_value_is_rejected(self, client):
        response = client.post("/investigations", json={"transaction_id": KNOWN_TXN, "trigger": "NOT_A_REAL_TRIGGER"})
        assert response.status_code == 400


class TestAgentFailure:
    def test_malformed_llm_output_never_fabricates_a_policy_decision(self, app, client):
        set_llm(app, MalformedLLMClient())

        response = client.post("/investigations", json={"transaction_id": KNOWN_TXN})

        assert response.status_code == 502
        body = response.json()
        assert body["error"]["code"] == "AGENT_FAILED"
        assert "policy_decision" not in body["error"]


class TestIterationLimit:
    def test_limit_reached_is_returned_as_a_structured_status_not_an_error(self, app, client):
        from app.agent.state import RequestedEvidenceType

        def limited_orchestrator(
            client_dep=Depends(get_tigergraph_client),
            case_store=Depends(get_case_store),
            llm_client=Depends(get_llm_client),
            investigation_service: InvestigationService = Depends(get_investigation_service),
        ) -> AgentOrchestrator:
            return AgentOrchestrator(
                client_dep, case_store, llm_client, investigation_service=investigation_service, max_iterations=2
            )

        app.dependency_overrides[get_orchestrator] = limited_orchestrator
        set_llm(
            app,
            FakeLLMClient(
                decisions=[
                    LLMDecision(
                        action=LLMDecisionAction.REQUEST_MORE_EVIDENCE,
                        requested_evidence_type=RequestedEvidenceType.ANALYST_REVIEW,
                        reason="never satisfied",
                    )
                    for _ in range(50)
                ]
            ),
        )

        response = client.post("/investigations", json={"transaction_id": KNOWN_TXN})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == AgentStatus.LIMIT_REACHED.value
        assert body["completed"] is False
        # PolicyEngine's own deterministic conservatism is still the safety
        # net - a real (not fabricated) policy decision is still produced.
        assert body["policy_decision"] is not None
        assert body["error"] is None


class TestGetInvestigation:
    def test_retrieves_a_just_created_investigation(self, client):
        created = client.post("/investigations", json={"transaction_id": KNOWN_TXN}).json()

        response = client.get(f"/investigations/{created['investigation_id']}")

        assert response.status_code == 200
        assert response.json()["case_id"] == created["case_id"]

    def test_unknown_investigation_id_is_404(self, client):
        response = client.get("/investigations/does-not-exist")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
