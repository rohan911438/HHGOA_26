"""Offline tests for app/investigation/service.py.

Every TigerGraph call is monkeypatched on app.tigergraph.queries (the
module app.investigation.tools imports as `queries`), exactly like
tests/unit/test_evidence_aggregate.py and
tests/unit/test_investigation_registry.py - no live connection involved.
`InvestigationService` is given an injected id_generator and clock
throughout, so nothing here depends on time.sleep() or the real wall
clock for determinism (only the timeout/ordering tests use real
time.sleep() on purpose, to exercise real threading).
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from itertools import count

import pytest

from app.evidence.models import EvidenceType, QueryStatus
from app.investigation import tools as t
from app.investigation.registry import build_default_registry
from app.investigation.service import DEFAULT_MAX_CONCURRENT_TOOLS, InvestigationService
from app.investigation.snapshot import (
    INVESTIGATION_PLAN,
    InvestigationSnapshot,
    InvestigationStatus,
)
from app.tigergraph.client import TigerGraphQueryError, TigerGraphUnavailable
from app.tigergraph.queries import (
    NetworkSummary,
    SharedEmailActivity,
    SharedEntityActivity,
    TransactionContext,
)


class FakeSettings:
    # classify_network does a real DNS check before pattern-matching -
    # same convention as test_investigation_registry.py.
    tg_host = "https://example.com"
    tg_timeout_seconds = 60


class FakeClient:
    settings = FakeSettings()


def fixed_clock(start: datetime = datetime(2026, 1, 1, tzinfo=UTC)):
    """A clock that advances by one microsecond per call - deterministic,
    always increasing (so started_at <= completed_at holds), never the
    real wall clock."""
    counter = count()

    def _clock() -> datetime:
        from datetime import timedelta

        return start + timedelta(microseconds=next(counter))

    return _clock


def fixed_ids(prefix: str = "inv-test"):
    counter = count(1)

    def _gen() -> str:
        return f"{prefix}-{next(counter)}"

    return _gen


def empty_entity(entity_type: str):
    return SharedEntityActivity(entity_type, "T1", None, [])


def all_empty_queries(monkeypatch, ctx: TransactionContext | None = None) -> None:
    monkeypatch.setattr(
        t.queries,
        "get_transaction_context",
        lambda client, txn: ctx
        or TransactionContext(
            transaction_id=txn, attributes={"is_fraud": False}, card=None, address=None,
            purchaser_email=None, recipient_email=None, device=None,
        ),
    )
    monkeypatch.setattr(t.queries, "find_shared_card_activity", lambda client, txn: empty_entity("Card"))
    monkeypatch.setattr(t.queries, "find_shared_device_activity", lambda client, txn: empty_entity("Device"))
    monkeypatch.setattr(t.queries, "find_shared_address_activity", lambda client, txn: empty_entity("Address"))
    monkeypatch.setattr(t.queries, "find_shared_email_activity", lambda client, txn: SharedEmailActivity("T1", None))
    monkeypatch.setattr(
        t.queries,
        "investigate_transaction_network",
        lambda client, txn: NetworkSummary(
            "T1",
            linked_entities=dict.fromkeys(
                ["Card", "Address", "EmailDomain_purchaser", "EmailDomain_recipient", "Device"], 0
            ),
            related_by_entity_type=dict.fromkeys(
                ["Card", "Address", "EmailDomain_purchaser", "EmailDomain_recipient", "Device"], 0
            ),
        ),
    )


@pytest.fixture
def service() -> InvestigationService:
    return InvestigationService(
        build_default_registry(), id_generator=fixed_ids(), clock=fixed_clock()
    )


# ---------------------------------------------------------------- success


class TestSuccessfulInvestigation:
    def test_all_success_produces_completed(self, monkeypatch, service):
        ctx = TransactionContext(
            transaction_id="T1", attributes={"is_fraud": True}, card={"id": "card-1", "type": "Card"},
            address=None, purchaser_email=None, recipient_email=None, device=None,
        )
        related = [{"transaction_id": "T2", "is_fraud": False, "transaction_amt": 1.0,
                     "transaction_dt": 1, "product_cd": "W"}]
        monkeypatch.setattr(t.queries, "get_transaction_context", lambda client, txn: ctx)
        monkeypatch.setattr(
            t.queries, "find_shared_card_activity",
            lambda client, txn: SharedEntityActivity("Card", txn, {"id": "card-1", "type": "Card"}, related),
        )
        monkeypatch.setattr(t.queries, "find_shared_device_activity", lambda client, txn: empty_entity("Device"))
        monkeypatch.setattr(t.queries, "find_shared_address_activity", lambda client, txn: empty_entity("Address"))
        monkeypatch.setattr(t.queries, "find_shared_email_activity", lambda client, txn: SharedEmailActivity(txn, None))
        monkeypatch.setattr(
            t.queries, "investigate_transaction_network",
            lambda client, txn: NetworkSummary(
                txn, {"Card": 1, "Address": 0, "EmailDomain_purchaser": 0, "EmailDomain_recipient": 0, "Device": 0},
                {"Card": 1, "Address": 0, "EmailDomain_purchaser": 0, "EmailDomain_recipient": 0, "Device": 0},
            ),
        )

        snap = service.investigate_transaction("T1", FakeClient())
        assert snap.status == InvestigationStatus.COMPLETED
        assert snap.tools_executed == list(INVESTIGATION_PLAN)
        assert snap.warnings == []
        assert snap.evidence_bundle.dataset_risk_score == 1.0

    def test_empty_results_still_complete_not_partial(self, monkeypatch, service):
        all_empty_queries(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        assert snap.status == InvestigationStatus.COMPLETED
        assert snap.coverage.empty_tools == 5  # all but context, which is SUCCESS
        assert snap.coverage.failed_tools == 0
        assert snap.warnings == []

    def test_snapshot_is_json_serializable(self, monkeypatch, service):
        all_empty_queries(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        import json

        dumped = snap.model_dump(mode="json")
        json.dumps(dumped)  # must not raise

    def test_investigation_id_is_distinct_from_evidence_and_transaction_id(self, monkeypatch, service):
        all_empty_queries(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        assert snap.investigation_id != snap.transaction_id
        assert all(snap.investigation_id not in e.evidence_id for e in snap.evidence_bundle.evidence)

    def test_deterministic_clock_and_id_injection_is_reproducible(self, monkeypatch):
        all_empty_queries(monkeypatch)
        svc_a = InvestigationService(build_default_registry(), id_generator=fixed_ids("x"), clock=fixed_clock())
        svc_b = InvestigationService(build_default_registry(), id_generator=fixed_ids("x"), clock=fixed_clock())
        snap_a = svc_a.investigate_transaction("T1", FakeClient())
        snap_b = svc_b.investigate_transaction("T1", FakeClient())
        assert snap_a.investigation_id == snap_b.investigation_id
        assert snap_a.started_at == snap_b.started_at
        assert snap_a.completed_at == snap_b.completed_at


# ---------------------------------------------------------------- failure handling


class TestContextFailureStopsTheInvestigation:
    def test_not_found_context_yields_failed_and_runs_nothing_else(self, monkeypatch, service):
        called = []

        def boom(client, txn):
            raise TigerGraphQueryError(f"transaction '{txn}' does not exist")

        def track(name):
            def _f(client, txn):
                called.append(name)
                return empty_entity(name)
            return _f

        monkeypatch.setattr(t.queries, "get_transaction_context", boom)
        monkeypatch.setattr(t.queries, "find_shared_card_activity", track("Card"))
        monkeypatch.setattr(t.queries, "find_shared_device_activity", track("Device"))
        monkeypatch.setattr(t.queries, "find_shared_address_activity", track("Address"))
        monkeypatch.setattr(t.queries, "find_shared_email_activity", lambda client, txn: SharedEmailActivity(txn, None))
        monkeypatch.setattr(t.queries, "investigate_transaction_network", track("Network"))

        snap = service.investigate_transaction("ghost", FakeClient())
        assert snap.status == InvestigationStatus.FAILED
        assert snap.tools_executed == ["get_transaction_context"]
        assert called == []  # the five other tools never ran
        assert any("NOT_FOUND" in w for w in snap.warnings)

    def test_context_unavailable_also_yields_failed(self, monkeypatch, service):
        def boom(client, txn):
            raise TigerGraphUnavailable("Could not mint a REST++ token from TG_SECRET: 500 Server Error")

        monkeypatch.setattr(t.queries, "get_transaction_context", boom)
        snap = service.investigate_transaction("T1", FakeClient())
        assert snap.status == InvestigationStatus.FAILED


class TestPartialFailure:
    def test_one_tool_error_yields_partial_not_failed(self, monkeypatch, service):
        all_empty_queries(monkeypatch)

        def boom(client, txn):
            raise TigerGraphUnavailable("Could not mint a REST++ token from TG_SECRET: 500 Server Error")

        monkeypatch.setattr(t.queries, "find_shared_address_activity", boom)

        snap = service.investigate_transaction("T1", FakeClient())
        assert snap.status == InvestigationStatus.PARTIAL
        assert snap.coverage.failed_tools == 1
        assert any("find_shared_address_activity" in w for w in snap.warnings)

    def test_error_status_evidence_is_preserved_as_error_not_dropped(self, monkeypatch, service):
        all_empty_queries(monkeypatch)

        def boom(client, txn):
            raise TigerGraphUnavailable("Could not mint a REST++ token from TG_SECRET: 500 Server Error")

        monkeypatch.setattr(t.queries, "find_shared_address_activity", boom)
        snap = service.investigate_transaction("T1", FakeClient())

        address_evidence = next(
            e for e in snap.evidence_bundle.evidence if e.evidence_type == EvidenceType.SHARED_ADDRESS
        )
        # Critical: an ERROR must never be silently reported as EMPTY -
        # the future uncertainty layer needs to know evidence is missing
        # because of a failure, not because nothing was found.
        assert address_evidence.status == QueryStatus.ERROR
        assert address_evidence.error is not None


# ---------------------------------------------------------------- ordering & concurrency


class TestDeterministicOrdering:
    def test_output_order_is_fixed_regardless_of_completion_order(self, monkeypatch, service):
        ctx = TransactionContext(
            transaction_id="T1", attributes={"is_fraud": False}, card=None, address=None,
            purchaser_email=None, recipient_email=None, device=None,
        )
        monkeypatch.setattr(t.queries, "get_transaction_context", lambda client, txn: ctx)

        def slow(entity_type, delay):
            def _f(client, txn):
                time.sleep(delay)
                return empty_entity(entity_type)
            return _f

        # Deliberately finishes in reverse-ish order: network fastest,
        # card slowest.
        monkeypatch.setattr(t.queries, "find_shared_card_activity", slow("Card", 0.15))
        monkeypatch.setattr(t.queries, "find_shared_device_activity", slow("Device", 0.1))
        monkeypatch.setattr(t.queries, "find_shared_address_activity", slow("Address", 0.05))
        monkeypatch.setattr(
            t.queries, "find_shared_email_activity",
            lambda client, txn: (time.sleep(0.02), SharedEmailActivity(txn, None))[1],
        )
        monkeypatch.setattr(
            t.queries, "investigate_transaction_network",
            lambda client, txn: NetworkSummary(
                txn, dict.fromkeys(["Card", "Address", "EmailDomain_purchaser", "EmailDomain_recipient", "Device"], 0),
                dict.fromkeys(["Card", "Address", "EmailDomain_purchaser", "EmailDomain_recipient", "Device"], 0),
            ),
        )

        snap = service.investigate_transaction("T1", FakeClient(), concurrent=True)
        assert snap.tools_executed == list(INVESTIGATION_PLAN)
        assert [entry.tool_name for entry in snap.execution_log] == list(INVESTIGATION_PLAN)


class TestConcurrencyBound:
    def test_max_concurrent_tools_is_never_exceeded(self, monkeypatch):
        ctx = TransactionContext(
            transaction_id="T1", attributes={"is_fraud": False}, card=None, address=None,
            purchaser_email=None, recipient_email=None, device=None,
        )
        monkeypatch.setattr(t.queries, "get_transaction_context", lambda client, txn: ctx)

        active = []
        lock = threading.Lock()
        max_seen = [0]

        def tracked(entity_type):
            def _f(client, txn):
                with lock:
                    active.append(1)
                    max_seen[0] = max(max_seen[0], len(active))
                time.sleep(0.1)
                with lock:
                    active.pop()
                return empty_entity(entity_type)
            return _f

        monkeypatch.setattr(t.queries, "find_shared_card_activity", tracked("Card"))
        monkeypatch.setattr(t.queries, "find_shared_device_activity", tracked("Device"))
        monkeypatch.setattr(t.queries, "find_shared_address_activity", tracked("Address"))
        monkeypatch.setattr(
            t.queries, "find_shared_email_activity",
            lambda client, txn: (time.sleep(0.1), SharedEmailActivity(txn, None))[1],
        )
        monkeypatch.setattr(
            t.queries, "investigate_transaction_network",
            lambda client, txn: (
                time.sleep(0.1),
                NetworkSummary(
                    txn, dict.fromkeys(["Card", "Address", "EmailDomain_purchaser", "EmailDomain_recipient", "Device"], 0),
                    dict.fromkeys(["Card", "Address", "EmailDomain_purchaser", "EmailDomain_recipient", "Device"], 0),
                ),
            )[1],
        )

        service = InvestigationService(build_default_registry(), max_concurrent_tools=2)
        snap = service.investigate_transaction("T1", FakeClient(), concurrent=True)
        assert max_seen[0] <= 2
        assert snap.status == InvestigationStatus.COMPLETED

    def test_default_max_concurrent_tools_is_conservative_not_unbounded(self):
        assert 1 <= DEFAULT_MAX_CONCURRENT_TOOLS < len(INVESTIGATION_PLAN) - 1

    def test_rejects_a_nonpositive_concurrency_limit(self):
        with pytest.raises(ValueError):
            InvestigationService(build_default_registry(), max_concurrent_tools=0)


# ---------------------------------------------------------------- timeouts


class TestOverallTimeout:
    def test_overall_timeout_marks_slow_tools_partial_without_hanging(self, monkeypatch):
        ctx = TransactionContext(
            transaction_id="T1", attributes={"is_fraud": False}, card=None, address=None,
            purchaser_email=None, recipient_email=None, device=None,
        )
        monkeypatch.setattr(t.queries, "get_transaction_context", lambda client, txn: ctx)
        monkeypatch.setattr(t.queries, "find_shared_card_activity", lambda client, txn: empty_entity("Card"))

        def very_slow(client, txn):
            time.sleep(2.0)
            return empty_entity("Device")

        monkeypatch.setattr(t.queries, "find_shared_device_activity", very_slow)
        monkeypatch.setattr(t.queries, "find_shared_address_activity", lambda client, txn: empty_entity("Address"))
        monkeypatch.setattr(t.queries, "find_shared_email_activity", lambda client, txn: SharedEmailActivity(txn, None))
        monkeypatch.setattr(
            t.queries, "investigate_transaction_network",
            lambda client, txn: NetworkSummary(
                txn, dict.fromkeys(["Card", "Address", "EmailDomain_purchaser", "EmailDomain_recipient", "Device"], 0),
                dict.fromkeys(["Card", "Address", "EmailDomain_purchaser", "EmailDomain_recipient", "Device"], 0),
            ),
        )

        service = InvestigationService(build_default_registry(), max_concurrent_tools=2)
        started = time.perf_counter()
        snap = service.investigate_transaction(
            "T1", FakeClient(), concurrent=True, overall_timeout_seconds=0.3
        )
        wall = time.perf_counter() - started

        assert wall < 1.5, "the overall timeout must bound wall time well below the 2s slow call"
        assert snap.status == InvestigationStatus.PARTIAL
        assert "find_shared_device_activity" not in snap.tools_executed
        assert any("INVESTIGATION_TIMEOUT" in w and "find_shared_device_activity" in w for w in snap.warnings)

    def test_per_tool_timeout_is_represented_explicitly(self, monkeypatch, service):
        all_empty_queries(monkeypatch)

        def slow(client, txn):
            time.sleep(1.0)
            return empty_entity("Address")

        monkeypatch.setattr(t.queries, "find_shared_address_activity", slow)

        # Force a tight per-tool timeout via a tiny overall budget so the
        # registry-level timeout (not the investigation-level skip path)
        # is what fires.
        snap = service.investigate_transaction(
            "T1", FakeClient(), concurrent=False, overall_timeout_seconds=0.1
        )
        # Either the tool ran and timed out (ERROR/TIMEOUT), or the
        # remaining budget was already exhausted before it could start
        # (INVESTIGATION_TIMEOUT) - both are explicit, neither hangs nor
        # is silently dropped.
        assert snap.status == InvestigationStatus.PARTIAL
        result = snap.tool_results.get("find_shared_address_activity")
        if result is not None:
            assert result.status == QueryStatus.ERROR


# ---------------------------------------------------------------- duplicate prevention


class TestDuplicatePrevention:
    def test_each_tool_is_called_at_most_once_per_investigation(self, monkeypatch, service):
        call_counts = {"card": 0}

        def counted(client, txn):
            call_counts["card"] += 1
            return empty_entity("Card")

        all_empty_queries(monkeypatch)
        monkeypatch.setattr(t.queries, "find_shared_card_activity", counted)

        service.investigate_transaction("T1", FakeClient())
        assert call_counts["card"] == 1


# ---------------------------------------------------------------- no verdict


class TestNoFraudVerdict:
    def test_no_verdict_field_exists_anywhere_on_the_snapshot_model(self):
        forbidden = {"fraud_verdict", "final_fraud_score", "is_fraud_verdict", "confidence"}
        assert forbidden.isdisjoint(InvestigationSnapshot.model_fields)

    def test_status_values_never_encode_a_fraud_conclusion(self):
        assert {s.value for s in InvestigationStatus} == {"COMPLETED", "PARTIAL", "FAILED"}
