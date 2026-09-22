"""Phase 2J - live infrastructure-validation test.

    transaction_id -> AgentOrchestrator (FakeLLMClient) -> BenchmarkResult

Runs `run_infrastructure_validation` against real TigerGraph data using
several transaction IDs from the IEEE-CIS development-fallback subset
(`data/dev/transactions_dev.csv` - the same subset earlier live tests
already load into the graph). This is NOT the official HHGoa 20-case
benchmark - see `app/benchmark/discovery.py` and
`docs/phase-2-benchmark-report.md`; the official dataset was not found
in this repository/environment. Every result asserts
`is_official_benchmark_case is False`.

Skips (does not fail) when TigerGraph is not currently reachable, same
as the rest of this package's live tests.
"""

from __future__ import annotations

import pytest

from app.benchmark import run_infrastructure_validation
from app.benchmark.models import BenchmarkResult
from app.case import CaseTrigger, InMemoryCaseStore
from app.config import get_settings
from app.tigergraph.client import get_client
from app.tigergraph.diagnostics import all_passed, run_checks

pytestmark = pytest.mark.tigergraph

# Development-fallback transaction IDs: one already used and confirmed
# reachable by tests/tigergraph/test_agent_live.py's KNOWN_TXN, plus the
# first rows of data/dev/transactions_dev.csv (deterministic, not
# cherry-picked for a favorable outcome - some may legitimately resolve
# to NOT_FOUND if they were not linked into the loaded subset, which the
# pipeline already handles gracefully; that is itself part of what this
# test validates).
DEV_FALLBACK_TXN_IDS = ["2987937", "2987000", "2987001", "2987002", "2987003"]


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


class TestInfrastructureValidation:
    def test_runs_every_case_and_labels_each_as_non_official(self, client, store):
        results = run_infrastructure_validation(
            client, store, DEV_FALLBACK_TXN_IDS, trigger=CaseTrigger.FRAUD_SIGNAL
        )

        assert len(results) == len(DEV_FALLBACK_TXN_IDS)
        for r in results:
            assert isinstance(r, BenchmarkResult)
            assert r.is_official_benchmark_case is False
            assert r.official_case_id is None

    def test_no_failure_is_silently_hidden(self, client, store):
        results = run_infrastructure_validation(client, store, DEV_FALLBACK_TXN_IDS)
        # Every result must be traceable to either a completed pipeline
        # run or a captured, non-empty error - never a silently dropped case.
        for r in results:
            assert r.error is not None or r.investigation_status is not None

    def test_completed_cases_are_fully_traceable(self, client, store):
        results = run_infrastructure_validation(client, store, DEV_FALLBACK_TXN_IDS)
        completed = [r for r in results if r.error is None]
        assert completed, "expected at least one transaction to complete the full pipeline"
        for r in completed:
            case_record = store.get(r.case_id)
            assert case_record is not None
            assert case_record.transaction_id == r.transaction_id
            assert r.case_status == case_record.status


def test_live_infra_validation_report(client, store):
    """Not a strict assertion test - prints the diagnostic trace this
    phase's report references."""
    results = run_infrastructure_validation(client, store, DEV_FALLBACK_TXN_IDS, trigger=CaseTrigger.FRAUD_SIGNAL)

    print("\n--- Phase 2J infrastructure validation (NOT an official benchmark run) ---")
    for r in results:
        print(
            f"txn={r.transaction_id} status={r.investigation_status.value} "
            f"case_id={r.case_id} action={r.recommended_action} "
            f"uncertainty={r.uncertainty_level} ms={r.execution_time_ms:.1f} error={r.error}"
        )
