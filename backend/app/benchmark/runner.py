"""Phase 2J - infrastructure validation runner.

Used only while the official benchmark is unavailable (`discovery.py`).
Exercises the real, already-verified pipeline

    transaction_id -> AgentOrchestrator.run() -> AgentInvestigationResult

end to end against real TigerGraph data, using this project's existing
`FakeLLMClient` (deterministic, no real LLM credentials or network call -
the same double `tests/tigergraph/test_agent_live.py` already uses for
live wiring checks), so it runs without OpenAI credentials.

Every `BenchmarkResult` this produces has `is_official_benchmark_case
=False` and `official_case_id=None`. It proves the pipeline is wired
end to end on real graph data; it makes no fraud-accuracy or
benchmark-score claim of any kind - there is nothing official to score
against yet.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from app.agent import (
    AgentOrchestrator,
    AgentStatus,
    FakeLLMClient,
    LLMDecision,
    LLMDecisionAction,
)
from app.benchmark.models import BenchmarkResult
from app.case import CaseStore, CaseTrigger
from app.tigergraph.client import TigerGraphClient

INFRA_VALIDATION_EXPLANATION = (
    "PHASE 2J INFRASTRUCTURE VALIDATION - diagnostic run against the "
    "IEEE-CIS development-fallback graph, not an official HHGoa benchmark "
    "result."
)


def run_infrastructure_validation(
    client: TigerGraphClient,
    case_store: CaseStore,
    transaction_ids: list[str],
    *,
    trigger: CaseTrigger = CaseTrigger.FRAUD_SIGNAL,
    max_iterations: int = 5,
) -> list[BenchmarkResult]:
    """Runs each transaction through one fresh `AgentOrchestrator` (a
    fresh `FakeLLMClient` per case, mirroring how a real agent would get
    a fresh LLM turn per investigation) and captures a `BenchmarkResult`
    per case, success or failure - failures are recorded, never hidden
    or retried until green."""
    results: list[BenchmarkResult] = []
    for txn_id in transaction_ids:
        results.append(_run_one(client, case_store, txn_id, trigger, max_iterations))
    return results


def _run_one(
    client: TigerGraphClient,
    case_store: CaseStore,
    txn_id: str,
    trigger: CaseTrigger,
    max_iterations: int,
) -> BenchmarkResult:
    llm = FakeLLMClient(
        decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="infra validation: fixed decision")],
        explanation=INFRA_VALIDATION_EXPLANATION,
    )
    orchestrator = AgentOrchestrator(client, case_store, llm, max_iterations=max_iterations)

    started = time.perf_counter()
    try:
        outcome = orchestrator.run(txn_id, trigger=trigger)
    except Exception as exc:  # noqa: BLE001 - captured for failure analysis, never swallowed
        elapsed_ms = (time.perf_counter() - started) * 1000
        return BenchmarkResult(
            case_id=f"error-{txn_id}",
            official_case_id=None,
            transaction_id=txn_id,
            is_official_benchmark_case=False,
            investigation_status=AgentStatus.FAILED,
            iteration_count=0,
            tool_call_count=0,
            execution_time_ms=elapsed_ms,
            error=f"{type(exc).__name__}: {exc}",
            executed_at=datetime.now(UTC),
        )
    elapsed_ms = (time.perf_counter() - started) * 1000

    case_record = case_store.get(outcome.case_id) if outcome.case_id else None

    return BenchmarkResult(
        case_id=outcome.case_id or f"no-case-{txn_id}",
        official_case_id=None,
        transaction_id=txn_id,
        is_official_benchmark_case=False,
        investigation_status=outcome.investigation_status,
        findings=outcome.findings,
        uncertainty_level=(
            outcome.uncertainty_assessment.uncertainty_level if outcome.uncertainty_assessment else None
        ),
        historical_context_count=len(outcome.historical_case_context),
        recommended_action=outcome.policy_decision.action if outcome.policy_decision else None,
        approval_required=outcome.policy_decision.approval_required if outcome.policy_decision else None,
        approval_route=outcome.policy_decision.approval_route if outcome.policy_decision else None,
        case_status=case_record.status if case_record else None,
        explanation=outcome.final_explanation,
        requested_evidence_count=len(outcome.requested_evidence),
        iteration_count=outcome.iterations,
        tool_call_count=outcome.tool_call_count,
        execution_time_ms=elapsed_ms,
        error=outcome.error,
        executed_at=datetime.now(UTC),
    )
