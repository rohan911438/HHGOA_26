"""Phase 2K §25 - live verification: the API against the real backend.

    HTTP request -> API -> Agent -> Investigation -> Uncertainty ->
    Context -> Policy -> Case -> HTTP response

Only the LLM is faked (`FakeLLMClient`, no real OpenAI credential/call,
same convention as every other live test in this repo -
tests/tigergraph/test_agent_live.py). TigerGraph is real. No expected
policy/uncertainty value is hardcoded here - each assertion instead
independently recomputes the same deterministic pipeline
(InvestigationService -> UncertaintyEngine -> PolicyEngine) and compares,
the same pattern test_agent_live.py already uses.

Skips (does not fail) when TigerGraph is not currently reachable.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.agent import FakeLLMClient, LLMDecision, LLMDecisionAction
from app.api import create_app
from app.api.dependencies import get_llm_client
from app.config import get_settings
from app.tigergraph.diagnostics import all_passed, run_checks

pytestmark = pytest.mark.tigergraph

KNOWN_TXN = "2987937"


@pytest.fixture(scope="module")
def live_client():
    settings = get_settings()
    if not settings.tg_configured:
        pytest.skip("TigerGraph not configured - set TG_HOST/TG_GRAPHNAME/TG_SECRET in .env")
    results = run_checks(settings)
    if not all_passed(results):
        detail = next((r.detail for r in results if r.ok is False), "unknown failure")
        pytest.skip(f"TigerGraph is not currently reachable/healthy - {detail}")

    app = create_app()
    app.dependency_overrides[get_llm_client] = lambda: FakeLLMClient(
        decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="deterministic fake: sufficient")],
        explanation="SYNTHETIC TEST EXPLANATION for the Phase 2K live API test.",
    )
    return TestClient(app)


def test_full_http_pipeline_against_the_live_backend(live_client):
    started = time.perf_counter()
    response = live_client.post("/investigations", json={"transaction_id": KNOWN_TXN, "trigger": "FRAUD_SIGNAL"})
    latency_ms = (time.perf_counter() - started) * 1000

    assert response.status_code == 200
    body = response.json()

    assert body["transaction_id"] == KNOWN_TXN
    assert body["case_id"] is not None
    assert body["status"] == "COMPLETED"
    assert body["completed"] is True
    assert body["error"] is None

    # Independently reproduce the deterministic pipeline (same pattern as
    # tests/tigergraph/test_agent_live.py) rather than hardcoding an
    # expected action/uncertainty value.
    from app.investigation import InvestigationService, build_default_registry
    from app.policy import PolicyEngine
    from app.tigergraph.client import get_client
    from app.uncertainty import UncertaintyEngine

    client = get_client(get_settings())
    service = InvestigationService(build_default_registry())
    snapshot = service.investigate_transaction(KNOWN_TXN, client)
    assessment = UncertaintyEngine().assess(snapshot)
    expected_policy = PolicyEngine().evaluate(snapshot, assessment)

    assert body["policy_decision"]["action"] == expected_policy.action.value
    assert body["policy_decision"]["approval_required"] == expected_policy.approval_required
    assert body["policy_decision"]["approval_route"] == expected_policy.approval_route.value
    assert body["policy_decision"]["executable"] is False
    assert body["uncertainty"]["uncertainty_level"] == assessment.uncertainty_level.value

    print(f"\n--- Phase 2K live API example: POST /investigations, transaction {KNOWN_TXN} ---")
    print("HTTP status:", response.status_code)
    print("case_id:", body["case_id"])
    print("status:", body["status"])
    print("uncertainty_level:", body["uncertainty"]["uncertainty_level"])
    print("action:", body["policy_decision"]["action"])
    print("approval_required:", body["policy_decision"]["approval_required"])
    print("approval_route:", body["policy_decision"]["approval_route"])
    print("executable:", body["policy_decision"]["executable"])
    print("explanation:", body["explanation"])
    print(f"latency_ms: {latency_ms:.1f}")


def test_case_retrieval_after_live_investigation(live_client):
    created = live_client.post("/investigations", json={"transaction_id": KNOWN_TXN}).json()

    started = time.perf_counter()
    response = live_client.get(f"/cases/{created['case_id']}")
    latency_ms = (time.perf_counter() - started) * 1000

    assert response.status_code == 200
    assert response.json()["transaction_id"] == KNOWN_TXN
    print(f"\ncase retrieval latency_ms: {latency_ms:.1f}")


def test_health_dependencies_against_the_live_backend(live_client):
    started = time.perf_counter()
    response = live_client.get("/health/dependencies")
    latency_ms = (time.perf_counter() - started) * 1000

    assert response.status_code == 200
    body = response.json()
    tg = next(d for d in body["dependencies"] if d["name"] == "tigergraph")
    assert tg["checked"] is True
    assert tg["healthy"] is True
    print(f"\n/health/dependencies latency_ms: {latency_ms:.1f}")
