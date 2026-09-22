"""Phase 2H - live end-to-end test: the agent orchestrator against the
real, loaded HHGOA_FRAUD graph.

    Agent (FakeLLMClient - deterministic, no real LLM/network call)
        v
    InvestigationService
        v
    UncertaintyEngine
        v
    CaseMemory
        v
    PolicyEngine
        v
    CaseManager
        v
    AgentInvestigationResult

Uses `FakeLLMClient`, not a real LLM - this is the deterministic "Phase
2H live" test (Phase 2H §22), distinct from the optional real-LLM
integration test (tests/integration/test_agent_real_llm.py, Phase 2H
§24), which requires actual credentials and is never part of this
required suite. Does not modify existing live investigation behavior -
only consumes InvestigationService's output via the agent.

Skips (does not fail) when TigerGraph is not currently reachable, using
this project's own diagnostics.
"""

from __future__ import annotations

import time

import pytest

from app.agent import AgentOrchestrator, AgentStatus, FakeLLMClient, LLMDecision, LLMDecisionAction
from app.case import CaseManager, CaseTrigger, InMemoryCaseStore
from app.config import get_settings
from app.tigergraph.client import get_client
from app.tigergraph.diagnostics import all_passed, run_checks

pytestmark = pytest.mark.tigergraph

KNOWN_TXN = "2987937"


@pytest.fixture(scope="module")
def client():
    settings = get_settings()
    if not settings.tg_configured:
        pytest.skip("TigerGraph not configured - set TG_HOST/TG_GRAPHNAME/TG_SECRET in .env")
    c = get_client(settings)
    results = run_checks(settings, c)
    if not all_passed(results):
        detail = next((r.detail for r in results if r.ok is False), "unknown failure")
        pytest.skip(f"TigerGraph is not currently reachable/healthy - {detail}")
    return c


@pytest.fixture
def store() -> InMemoryCaseStore:
    return InMemoryCaseStore()


class TestLiveAgentPipeline:
    def test_full_pipeline_produces_a_traceable_result(self, client, store):
        llm = FakeLLMClient(
            decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="deterministic fake: sufficient")],
            explanation="SYNTHETIC TEST EXPLANATION for the live Phase 2H pipeline test.",
        )
        orchestrator = AgentOrchestrator(client, store, llm, max_iterations=5)
        result = orchestrator.run(KNOWN_TXN, trigger=CaseTrigger.FRAUD_SIGNAL)

        assert result.investigation_status == AgentStatus.COMPLETED
        assert result.completed is True
        assert result.case_id is not None
        assert result.transaction_id == KNOWN_TXN
        assert result.iterations == 1
        assert result.tool_call_count == 1
        assert result.error is None

    def test_deterministic_components_remain_authoritative(self, client, store):
        from app.investigation import InvestigationService, build_default_registry
        from app.policy import PolicyEngine
        from app.uncertainty import UncertaintyEngine

        llm = FakeLLMClient(decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="sufficient")])
        orchestrator = AgentOrchestrator(client, store, llm, max_iterations=5)
        result = orchestrator.run(KNOWN_TXN)

        # Independently reproduce the deterministic pipeline and confirm
        # the agent's structured fields match exactly - never
        # LLM-perturbed. Do not expect specific LLM wording; verify
        # structured fields only (Phase 2H §22).
        service = InvestigationService(build_default_registry())
        snapshot = service.investigate_transaction(KNOWN_TXN, client)
        assessment = UncertaintyEngine().assess(snapshot)
        expected_policy = PolicyEngine().evaluate(snapshot, assessment)

        assert result.policy_decision.action == expected_policy.action
        assert result.policy_decision.approval_required == expected_policy.approval_required
        assert result.policy_decision.approval_route == expected_policy.approval_route
        assert result.uncertainty_assessment.evidence_coverage == assessment.evidence_coverage
        assert result.uncertainty_assessment.uncertainty_level == assessment.uncertainty_level

    def test_traceability_from_result_back_to_evidence_and_case(self, client, store):
        llm = FakeLLMClient(decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="sufficient")])
        orchestrator = AgentOrchestrator(client, store, llm, max_iterations=5)
        result = orchestrator.run(KNOWN_TXN)

        case_record = CaseManager(store).get_case(result.case_id)
        assert case_record.transaction_id == KNOWN_TXN
        assert set(result.policy_decision.evidence_ids) <= set(case_record.evidence_ids)
        assert case_record.actions[-1].action == result.policy_decision.action
        assert case_record.actions[-1].executed is False

    def test_no_arbitrary_tigergraph_access_from_the_agent_package(self):
        """Static, not just behavioral: confirm the orchestrator's only
        TigerGraph-facing import is the InvestigationService it composes,
        never app.tigergraph.queries directly."""
        import ast
        from pathlib import Path

        import app.agent.orchestrator as orch_module

        tree = ast.parse(Path(orch_module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.tigergraph.queries":
                pytest.fail("app.agent.orchestrator must never import app.tigergraph.queries directly")

    def test_performance(self, client, store):
        llm = FakeLLMClient(decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="sufficient")])

        started = time.perf_counter()
        orchestrator = AgentOrchestrator(client, store, llm, max_iterations=5)
        result = orchestrator.run(KNOWN_TXN)
        total_ms = (time.perf_counter() - started) * 1000

        print(f"\nfull agent run (1 iteration, real TigerGraph): {total_ms:.1f} ms")
        print(f"tool_call_count: {result.tool_call_count}, iterations: {result.iterations}")
        # No strict upper bound asserted - dominated by live TigerGraph
        # latency (seconds), which this project has already measured and
        # documented (Phase 2D docs). This just records the number.
        assert result.tool_call_count == 1


def test_live_example_report(client, store):
    """Not a strict assertion test - prints the exact live example this
    phase's final report references, against the current graph state."""
    llm = FakeLLMClient(
        decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="deterministic fake: sufficient")],
        explanation="SYNTHETIC TEST EXPLANATION.",
    )
    orchestrator = AgentOrchestrator(client, store, llm, max_iterations=5)
    result = orchestrator.run(KNOWN_TXN, trigger=CaseTrigger.FRAUD_SIGNAL)

    print("\n--- Phase 2H live example: transaction", KNOWN_TXN, "---")
    print("case_id:", result.case_id)
    print("iterations:", result.iterations, "tool_call_count:", result.tool_call_count)
    print("investigation_status:", result.investigation_status.value)
    print("uncertainty_level:", result.uncertainty_assessment.uncertainty_level.value)
    print("overall_uncertainty:", result.uncertainty_assessment.overall_uncertainty)
    print("policy action:", result.policy_decision.action.value)
    print("approval_required:", result.policy_decision.approval_required)
    print("approval_route:", result.policy_decision.approval_route.value)
    print("executable:", result.policy_decision.executable)
    print("next_step:", result.next_step)
    print("requested_evidence:", result.requested_evidence)
    print("historical_case_context:", result.historical_case_context)
    assert result.investigation_status == AgentStatus.COMPLETED
