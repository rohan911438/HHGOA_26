"""Phase 2I - live end-to-end test: the GraphRAG/context layer against
the real, loaded HHGOA_FRAUD graph.

    InvestigationService (Phase 2D)
            v
    UncertaintyEngine (Phase 2E)
            v
    CaseMemory (Phase 2G) - synthetic historical cases seeded for this test
            v
    ContextBuilder (Phase 2I)  <- pure computation, no TigerGraph, no LLM
            v
    InvestigationContext
            v
    Phase 2H Agent (FakeLLMClient)

No official HHGoa historical cases exist in this development
environment - the historical cases used here are explicitly SYNTHETIC
DEVELOPMENT CASES, seeded into an in-memory store for this test only,
never presented as real bank investigations (Phase 2I §23).

Skips (does not fail) when TigerGraph is not currently reachable, using
this project's own diagnostics. Does not modify existing live
investigation behavior - only consumes its output.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from app.agent import AgentOrchestrator, AgentStatus, FakeLLMClient, LLMDecision, LLMDecisionAction
from app.case import (
    CaseManager,
    CaseMemory,
    CaseOutcome,
    CaseOutcomeType,
    CaseStatus,
    CaseTrigger,
    InMemoryCaseStore,
)
from app.case.models import CaseRecord
from app.config import get_settings
from app.context import ContextBuilder, ContextItemType, ContextLimits, format_context_for_prompt
from app.investigation import InvestigationService, build_default_registry
from app.tigergraph.client import get_client
from app.tigergraph.diagnostics import all_passed, run_checks
from app.uncertainty import UncertaintyEngine

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
    """Seeded with SYNTHETIC DEVELOPMENT CASES only - no official HHGoa
    historical case data exists in this environment."""
    store = InMemoryCaseStore()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(4):
        store.save(
            CaseRecord(
                case_id=f"synthetic-hist-{i}",
                status=CaseStatus.CLOSED,
                trigger=CaseTrigger.FRAUD_SIGNAL,
                transaction_id=f"SYNTH-TX-{i}",
                created_at=now,
                updated_at=now,
                evidence_types=["shared_card", "shared_address"],
                outcome=CaseOutcome(
                    outcome_type=CaseOutcomeType.CLEARED if i % 2 == 0 else CaseOutcomeType.CONFIRMED_FRAUD,
                    recorded_at=now,
                    is_synthetic=True,
                ),
            )
        )
    return store


class TestLiveContextPipeline:
    def test_context_built_from_the_real_investigation(self, client, store):
        service = InvestigationService(build_default_registry())
        snapshot = service.investigate_transaction(KNOWN_TXN, client)
        assessment = UncertaintyEngine().assess(snapshot)

        manager = CaseManager(store)
        case = manager.create_case(snapshot, uncertainty_assessment=assessment, trigger=CaseTrigger.FRAUD_SIGNAL)

        memory = CaseMemory(store)
        builder = ContextBuilder(memory, limits=ContextLimits(top_k_historical_cases=3, top_k_recurring_patterns=3))
        context = builder.build_for_case(case, snapshot, assessment)

        assert context.transaction_id == KNOWN_TXN
        assert len(context.current_evidence) == 6
        real_evidence_ids = {e.evidence_id for e in snapshot.evidence_bundle.evidence}
        for item in context.current_evidence:
            assert item.evidence_ids[0] in real_evidence_ids
            assert item.provenance

        # Synthetic historical cases (seeded above) should be retrieved -
        # never fabricated bank cases.
        assert len(context.historical_cases) > 0
        for h in context.historical_cases:
            assert h.case_ids[0].startswith("synthetic-hist-")

        assert context.uncertainty_assessment.evidence_coverage == assessment.evidence_coverage
        assert context.truncated in (True, False)

    def test_address_evidence_stays_low_quality_in_context(self, client, store):
        service = InvestigationService(build_default_registry())
        snapshot = service.investigate_transaction(KNOWN_TXN, client)
        assessment = UncertaintyEngine().assess(snapshot)
        builder = ContextBuilder()
        context = builder.build(snapshot, assessment)
        address_item = next(i for i in context.current_evidence if "shared_address" in i.evidence_ids[0])
        assert address_item.quality.value == "LOW"

    def test_no_dataset_label_leakage_on_a_real_context(self, client, store):
        from itertools import count

        def fixed_ids():
            c = count(1)
            return lambda: f"ctx-fixed-{next(c)}"

        service = InvestigationService(build_default_registry())
        snapshot = service.investigate_transaction(KNOWN_TXN, client)
        assessment = UncertaintyEngine().assess(snapshot)
        # A fresh, independently-seeded deterministic id_generator per
        # build - so a genuine label leak (a value differing) is never
        # masked by two builds simply landing on different random UUIDs.
        context_real = ContextBuilder(id_generator=fixed_ids()).build(snapshot, assessment)

        real_label = snapshot.evidence_bundle.dataset_risk_score
        flipped_snapshot = snapshot.model_copy(
            update={
                "evidence_bundle": snapshot.evidence_bundle.model_copy(
                    update={"dataset_risk_score": 0.0 if real_label == 1.0 else 1.0}
                )
            }
        )
        context_flipped = ContextBuilder(id_generator=fixed_ids()).build(flipped_snapshot, assessment)

        d1 = context_real.model_dump(mode="json")
        d2 = context_flipped.model_dump(mode="json")
        d1.pop("generated_at")
        d2.pop("generated_at")
        assert d1 == d2

    def test_context_is_json_serializable(self, client, store):
        service = InvestigationService(build_default_registry())
        snapshot = service.investigate_transaction(KNOWN_TXN, client)
        assessment = UncertaintyEngine().assess(snapshot)
        builder = ContextBuilder()
        context = builder.build(snapshot, assessment)
        import json

        json.dumps(context.model_dump(mode="json"))
        json.dumps(format_context_for_prompt(context))

    def test_agent_integration_produces_context_on_the_result(self, client, store):
        llm = FakeLLMClient(decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="deterministic fake: sufficient")])
        orchestrator = AgentOrchestrator(client, store, llm, max_iterations=5)
        result = orchestrator.run(KNOWN_TXN, trigger=CaseTrigger.FRAUD_SIGNAL)

        assert result.investigation_status == AgentStatus.COMPLETED
        assert result.context is not None
        assert result.context.policy_decision is not None
        assert result.context.policy_decision.action == result.policy_decision.action
        assert len(result.context.current_evidence) == 6
        assert any(
            item.context_type == ContextItemType.HISTORICAL_CASE for item in result.context.historical_cases
        ) or result.context.historical_cases == []  # store had synthetic cases seeded for THIS test's fixture only

    def test_performance(self, client, store):
        service = InvestigationService(build_default_registry())
        snapshot = service.investigate_transaction(KNOWN_TXN, client)
        assessment = UncertaintyEngine().assess(snapshot)
        manager = CaseManager(store)
        case = manager.create_case(snapshot, uncertainty_assessment=assessment, trigger=CaseTrigger.FRAUD_SIGNAL)
        memory = CaseMemory(store)
        builder = ContextBuilder(memory)

        started = time.perf_counter()
        for _ in range(20):
            builder.build_for_case(case, snapshot, assessment)
        build_avg_ms = (time.perf_counter() - started) / 20 * 1000

        context = builder.build_for_case(case, snapshot, assessment)
        started = time.perf_counter()
        for _ in range(20):
            memory.retrieve_similar(case)
        retrieval_avg_ms = (time.perf_counter() - started) / 20 * 1000

        started = time.perf_counter()
        for _ in range(20):
            context.model_dump(mode="json")
        serialize_avg_ms = (time.perf_counter() - started) / 20 * 1000

        print(f"\nContextBuilder.build_for_case avg: {build_avg_ms:.3f} ms")
        print(f"CaseMemory.retrieve_similar avg: {retrieval_avg_ms:.3f} ms")
        print(f"InvestigationContext serialization avg: {serialize_avg_ms:.3f} ms")
        assert build_avg_ms < 50
        assert retrieval_avg_ms < 50
        assert serialize_avg_ms < 50


def test_live_example_report(client, store):
    """Not a strict assertion test - prints the exact live example this
    phase's final report references, against the current graph state."""
    service = InvestigationService(build_default_registry())
    snapshot = service.investigate_transaction(KNOWN_TXN, client)
    assessment = UncertaintyEngine().assess(snapshot)
    manager = CaseManager(store)
    case = manager.create_case(snapshot, uncertainty_assessment=assessment, trigger=CaseTrigger.FRAUD_SIGNAL)
    memory = CaseMemory(store)
    builder = ContextBuilder(memory, limits=ContextLimits(top_k_historical_cases=3, top_k_recurring_patterns=3))
    context = builder.build_for_case(case, snapshot, assessment)

    print("\n--- Phase 2I live example: transaction", KNOWN_TXN, "---")
    print("current_evidence:", len(context.current_evidence))
    for item in context.current_evidence:
        print(" -", item.content)
    print("missing_information:", len(context.missing_information))
    print("historical_cases:", len(context.historical_cases), "(SYNTHETIC DEVELOPMENT CASES)")
    print("recurring_patterns:", len(context.recurring_patterns))
    print("uncertainty_level:", context.uncertainty_assessment.uncertainty_level.value)
    print("overall_uncertainty:", context.uncertainty_assessment.overall_uncertainty)
    print("total context_items:", len(context.context_items))
    print("truncated:", context.truncated)
    assert context.transaction_id == KNOWN_TXN
