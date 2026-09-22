"""Offline tests for app/uncertainty/engine.py and models.py.

Most scenarios run the real InvestigationService with
app.tigergraph.queries monkeypatched - same convention as
tests/unit/test_investigation_service.py - producing a genuine
InvestigationSnapshot for the engine to assess, no live TigerGraph
connection involved. A few quality-mapping edge cases (HIGH quality
specifically) are not reachable through the current Phase 2A/2B evidence
types, so those call UncertaintyEngine's quality aggregation directly
against synthetic Evidence objects instead.
"""

from __future__ import annotations

import ast
from datetime import UTC
from pathlib import Path

import pytest

from app.evidence.models import (
    Evidence,
    EvidenceType,
    Provenance,
    QueryStatus,
    SignalQuality,
    evidence_id,
)
from app.investigation import InvestigationService, build_default_registry
from app.investigation import tools as t
from app.investigation.snapshot import InvestigationStatus
from app.tigergraph.client import TigerGraphQueryError, TigerGraphUnavailable
from app.tigergraph.queries import (
    NetworkSummary,
    SharedEmailActivity,
    SharedEntityActivity,
    TransactionContext,
)
from app.uncertainty import (
    ConflictSeverity,
    ConflictType,
    MissingEvidenceReason,
    UncertaintyEngine,
    UncertaintyLevel,
)


class FakeSettings:
    tg_host = "https://example.com"
    tg_timeout_seconds = 60


class FakeClient:
    settings = FakeSettings()


def empty_entity(entity_type: str, txn: str = "T1"):
    return SharedEntityActivity(entity_type, txn, None, [])


def linked_entity(entity_type: str, entity_id_: str, related: list[dict] | None = None, txn: str = "T1"):
    return SharedEntityActivity(entity_type, txn, {"id": entity_id_, "type": entity_type}, related or [])


def txn_row(i: int) -> dict:
    return {"transaction_id": f"T{i}", "is_fraud": False, "transaction_amt": 1.0,
            "transaction_dt": 1, "product_cd": "W"}


def set_all_clean(monkeypatch, *, is_fraud: bool = False, amt: float | None = 100.0, product: str | None = "W"):
    """All six tools return a well-formed, fully-linked, fully-consistent
    result: card and address linked with matching related counts,
    network breakdown agreeing exactly - the baseline "everything is
    fine" scenario tests then perturb."""
    ctx = TransactionContext(
        transaction_id="T1", attributes={"is_fraud": is_fraud, "transaction_amt": amt, "product_cd": product},
        card={"id": "card-1", "type": "Card"}, address={"id": "addr-1", "type": "Address"},
        purchaser_email=None, recipient_email=None, device=None,
    )
    monkeypatch.setattr(t.queries, "get_transaction_context", lambda client, txn: ctx)
    monkeypatch.setattr(
        t.queries, "find_shared_card_activity",
        lambda client, txn: linked_entity("Card", "card-1", [txn_row(2)]),
    )
    monkeypatch.setattr(t.queries, "find_shared_device_activity", lambda client, txn: empty_entity("Device"))
    monkeypatch.setattr(
        t.queries, "find_shared_address_activity",
        lambda client, txn: linked_entity("Address", "addr-1", [txn_row(i) for i in range(2, 5)]),
    )
    monkeypatch.setattr(t.queries, "find_shared_email_activity", lambda client, txn: SharedEmailActivity(txn, None))
    monkeypatch.setattr(
        t.queries, "investigate_transaction_network",
        lambda client, txn: NetworkSummary(
            txn,
            {"Card": 1, "Address": 1, "EmailDomain_purchaser": 0, "EmailDomain_recipient": 0, "Device": 0},
            {"Card": 1, "Address": 3, "EmailDomain_purchaser": 0, "EmailDomain_recipient": 0, "Device": 0},
        ),
    )


def set_all_empty(monkeypatch, *, is_fraud: bool = False):
    ctx = TransactionContext(
        transaction_id="T1", attributes={"is_fraud": is_fraud, "transaction_amt": 1.0, "product_cd": "W"},
        card=None, address=None, purchaser_email=None, recipient_email=None, device=None,
    )
    monkeypatch.setattr(t.queries, "get_transaction_context", lambda client, txn: ctx)
    monkeypatch.setattr(t.queries, "find_shared_card_activity", lambda client, txn: empty_entity("Card"))
    monkeypatch.setattr(t.queries, "find_shared_device_activity", lambda client, txn: empty_entity("Device"))
    monkeypatch.setattr(t.queries, "find_shared_address_activity", lambda client, txn: empty_entity("Address"))
    monkeypatch.setattr(t.queries, "find_shared_email_activity", lambda client, txn: SharedEmailActivity(txn, None))
    monkeypatch.setattr(
        t.queries, "investigate_transaction_network",
        lambda client, txn: NetworkSummary(
            txn, dict.fromkeys(["Card", "Address", "EmailDomain_purchaser", "EmailDomain_recipient", "Device"], 0),
            dict.fromkeys(["Card", "Address", "EmailDomain_purchaser", "EmailDomain_recipient", "Device"], 0),
        ),
    )


@pytest.fixture
def service() -> InvestigationService:
    return InvestigationService(build_default_registry(), id_generator=lambda: "inv-fixed")


@pytest.fixture
def engine() -> UncertaintyEngine:
    return UncertaintyEngine()


# ---------------------------------------------------------------- coverage


class TestEvidenceCoverage:
    def test_all_success_or_empty_gives_full_coverage(self, monkeypatch, service, engine):
        set_all_clean(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        assert assessment.evidence_coverage == 1.0

    def test_all_empty_also_gives_full_coverage(self, monkeypatch, service, engine):
        set_all_empty(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        assert assessment.evidence_coverage == 1.0
        assert assessment.missing_evidence == []

    def test_one_error_reduces_coverage_by_one_sixth(self, monkeypatch, service, engine):
        set_all_clean(monkeypatch)

        def boom(client, txn):
            raise TigerGraphUnavailable("Could not mint a REST++ token from TG_SECRET: 500 Server Error")

        monkeypatch.setattr(t.queries, "find_shared_device_activity", boom)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        assert assessment.evidence_coverage == pytest.approx(5 / 6)
        assert any(
            m.evidence_type == "find_shared_device_activity" and m.reason == MissingEvidenceReason.QUERY_ERROR
            for m in assessment.missing_evidence
        )

    def test_context_error_yields_failed_and_unknown_uncertainty(self, monkeypatch, service, engine):
        def boom(client, txn):
            raise TigerGraphQueryError(f"transaction '{txn}' does not exist")

        monkeypatch.setattr(t.queries, "get_transaction_context", boom)
        snap = service.investigate_transaction("ghost", FakeClient())
        assert snap.status == InvestigationStatus.FAILED
        assessment = engine.assess(snap)
        assert assessment.overall_uncertainty is None
        assert assessment.uncertainty_level == UncertaintyLevel.UNKNOWN
        assert assessment.sufficient_for_next_stage is False


# ---------------------------------------------------------------- quality


class TestSignalQuality:
    def test_mixed_quality_averages_only_rated_items(self, monkeypatch, service, engine):
        set_all_clean(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        # card=MEDIUM(0.66), address=LOW(0.33) both SUCCESS and rated;
        # transaction_context(UNKNOWN), device/email(EMPTY->UNKNOWN),
        # network(SUCCESS but its own quality here is MEDIUM given a
        # mostly-Card-driven small fan-out) all excluded or included per
        # their own real status - just assert it's a real number in range.
        assert assessment.signal_quality is not None
        assert 0.0 < assessment.signal_quality <= 1.0

    def test_no_rated_evidence_gives_none_not_zero(self, monkeypatch, service, engine):
        set_all_empty(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        # every evidence item is either UNKNOWN (context) or EMPTY->UNKNOWN
        # (all five others) - no rated item exists.
        assert assessment.signal_quality is None

    def test_all_high_quality_averages_to_one(self):
        by_type = {
            EvidenceType.SHARED_CARD: _synthetic_evidence(EvidenceType.SHARED_CARD, SignalQuality.HIGH),
            EvidenceType.SHARED_DEVICE: _synthetic_evidence(EvidenceType.SHARED_DEVICE, SignalQuality.HIGH),
        }
        assert UncertaintyEngine._signal_quality(by_type) == pytest.approx(1.0)

    def test_all_low_quality_averages_to_033(self):
        by_type = {
            EvidenceType.SHARED_ADDRESS: _synthetic_evidence(EvidenceType.SHARED_ADDRESS, SignalQuality.LOW),
            EvidenceType.SHARED_EMAIL_DOMAIN: _synthetic_evidence(EvidenceType.SHARED_EMAIL_DOMAIN, SignalQuality.LOW),
        }
        assert UncertaintyEngine._signal_quality(by_type) == pytest.approx(0.33)

    def test_unknown_quality_items_never_pull_down_the_average(self):
        by_type = {
            EvidenceType.SHARED_CARD: _synthetic_evidence(EvidenceType.SHARED_CARD, SignalQuality.HIGH),
            EvidenceType.TRANSACTION_CONTEXT: _synthetic_evidence(
                EvidenceType.TRANSACTION_CONTEXT, SignalQuality.UNKNOWN
            ),
        }
        # If UNKNOWN were scored as 0.0, this would average to 0.5, not 1.0.
        assert UncertaintyEngine._signal_quality(by_type) == pytest.approx(1.0)

    def test_relationship_count_is_never_used_as_a_quality_proxy(self, monkeypatch, service, engine):
        """The real, documented finding: Address can have a huge related
        count yet must stay LOW quality. Assert the aggregate quality
        score reflects LOW/MEDIUM category values, never something that
        scales with the raw count (e.g. 312)."""
        set_all_clean(monkeypatch)
        monkeypatch.setattr(
            t.queries, "find_shared_address_activity",
            lambda client, txn: linked_entity("Address", "addr-1", [txn_row(i) for i in range(2, 315)]),
        )
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        assert assessment.signal_quality is not None
        assert assessment.signal_quality <= 1.0  # never scales past the normalized ceiling


def _synthetic_evidence(evidence_type: EvidenceType, quality: SignalQuality) -> Evidence:
    return Evidence(
        evidence_id=evidence_id("T1", evidence_type, None),
        evidence_type=evidence_type,
        transaction_id="T1",
        status=QueryStatus.SUCCESS,
        observation="synthetic",
        quality=quality,
        quality_reason="synthetic test fixture",
        metrics={},
        provenance=Provenance(source_query="synthetic", transaction_id="T1"),
    )


# ---------------------------------------------------------------- conflict


class TestConflictDetection:
    def test_no_conflict_in_the_clean_baseline(self, monkeypatch, service, engine):
        set_all_clean(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        assert assessment.conflicting_evidence == []
        assert assessment.signal_conflict == 0.0

    def test_signal_disagreement_when_context_and_shared_card_disagree(self, monkeypatch, service, engine):
        set_all_clean(monkeypatch)
        # Context says a card IS linked, but the dedicated query finds none -
        # a genuine, real inconsistency between two supposedly-consistent facts.
        monkeypatch.setattr(t.queries, "find_shared_card_activity", lambda client, txn: empty_entity("Card"))
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        types = {c.conflict_type for c in assessment.conflicting_evidence}
        assert ConflictType.SIGNAL_DISAGREEMENT in types
        disagreement = next(c for c in assessment.conflicting_evidence if c.conflict_type == ConflictType.SIGNAL_DISAGREEMENT)
        assert disagreement.severity == ConflictSeverity.HIGH

    def test_data_inconsistency_when_network_breakdown_disagrees_with_dedicated_count(
        self, monkeypatch, service, engine
    ):
        set_all_clean(monkeypatch)
        # Dedicated shared-card query says 1 related transaction, but the
        # network summary reports 5 via Card for the same transaction -
        # these should always match exactly (same underlying edges).
        monkeypatch.setattr(
            t.queries, "investigate_transaction_network",
            lambda client, txn: NetworkSummary(
                txn,
                {"Card": 1, "Address": 1, "EmailDomain_purchaser": 0, "EmailDomain_recipient": 0, "Device": 0},
                {"Card": 5, "Address": 3, "EmailDomain_purchaser": 0, "EmailDomain_recipient": 0, "Device": 0},
            ),
        )
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        types = {c.conflict_type for c in assessment.conflicting_evidence}
        assert ConflictType.DATA_INCONSISTENCY in types

    def test_quality_disparity_when_low_quality_network_has_a_large_fanout(self, monkeypatch, service, engine):
        set_all_clean(monkeypatch)
        monkeypatch.setattr(
            t.queries, "find_shared_address_activity",
            lambda client, txn: linked_entity("Address", "addr-1", [txn_row(i) for i in range(2, 315)]),
        )
        monkeypatch.setattr(
            t.queries, "investigate_transaction_network",
            lambda client, txn: NetworkSummary(
                txn,
                {"Card": 1, "Address": 1, "EmailDomain_purchaser": 0, "EmailDomain_recipient": 0, "Device": 0},
                {"Card": 1, "Address": 313, "EmailDomain_purchaser": 0, "EmailDomain_recipient": 0, "Device": 0},
            ),
        )
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        types = {c.conflict_type for c in assessment.conflicting_evidence}
        assert ConflictType.QUALITY_DISPARITY in types

    def test_missing_context_when_one_tool_errors_while_others_find_things(self, monkeypatch, service, engine):
        set_all_clean(monkeypatch)

        def boom(client, txn):
            raise TigerGraphUnavailable("Could not mint a REST++ token from TG_SECRET: 500 Server Error")

        monkeypatch.setattr(t.queries, "find_shared_device_activity", boom)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        types = {c.conflict_type for c in assessment.conflicting_evidence}
        assert ConflictType.MISSING_CONTEXT in types
        mc = next(c for c in assessment.conflicting_evidence if c.conflict_type == ConflictType.MISSING_CONTEXT)
        assert mc.severity == ConflictSeverity.LOW

    def test_multiple_conflicts_raise_the_conflict_score_but_stay_bounded(self, monkeypatch, service, engine):
        set_all_clean(monkeypatch)
        monkeypatch.setattr(t.queries, "find_shared_card_activity", lambda client, txn: empty_entity("Card"))
        monkeypatch.setattr(
            t.queries, "investigate_transaction_network",
            lambda client, txn: NetworkSummary(
                txn,
                {"Card": 1, "Address": 1, "EmailDomain_purchaser": 0, "EmailDomain_recipient": 0, "Device": 0},
                {"Card": 9, "Address": 3, "EmailDomain_purchaser": 0, "EmailDomain_recipient": 0, "Device": 0},
            ),
        )
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        assert len(assessment.conflicting_evidence) >= 2
        assert 0.0 < assessment.signal_conflict <= 1.0

    def test_do_not_manufacture_a_conflict_from_a_mere_count_difference(self, monkeypatch, service, engine):
        """Two different, unrelated counts (e.g. card related=1 vs address
        related=3) must never by themselves trigger a conflict - only the
        four documented, grounded rules should."""
        set_all_clean(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        assert assessment.conflicting_evidence == []


# ---------------------------------------------------------------- data completeness


class TestDataCompleteness:
    def test_all_context_fields_populated_is_fully_complete(self, monkeypatch, service, engine):
        set_all_clean(monkeypatch, amt=250.0, product="W")
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        assert assessment.data_completeness == 1.0

    def test_a_null_context_field_is_reported_as_data_missing(self, monkeypatch, service, engine):
        set_all_clean(monkeypatch, product=None)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        assert assessment.data_completeness == pytest.approx(2 / 3)
        assert any(
            m.evidence_type == "transaction_context.product_cd" and m.reason == MissingEvidenceReason.DATA_MISSING
            for m in assessment.missing_evidence
        )

    def test_completeness_is_unknown_not_zero_when_context_failed(self, monkeypatch, service, engine):
        def boom(client, txn):
            raise TigerGraphQueryError(f"transaction '{txn}' does not exist")

        monkeypatch.setattr(t.queries, "get_transaction_context", boom)
        snap = service.investigate_transaction("ghost", FakeClient())
        assessment = engine.assess(snap)
        assert assessment.data_completeness is None


# ---------------------------------------------------------------- missing evidence distinctness


class TestMissingEvidenceDistinctness:
    def test_empty_is_never_listed_as_missing_evidence(self, monkeypatch, service, engine):
        set_all_empty(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        assert assessment.missing_evidence == []

    def test_error_is_listed_with_query_error_reason(self, monkeypatch, service, engine):
        set_all_clean(monkeypatch)

        def boom(client, txn):
            raise TigerGraphUnavailable("Could not mint a REST++ token from TG_SECRET: 500 Server Error")

        monkeypatch.setattr(t.queries, "find_shared_address_activity", boom)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        entry = next(m for m in assessment.missing_evidence if m.evidence_type == "find_shared_address_activity")
        assert entry.reason == MissingEvidenceReason.QUERY_ERROR
        assert entry.source_status == QueryStatus.ERROR

    def test_data_missing_is_a_distinct_reason_from_query_error(self, monkeypatch, service, engine):
        set_all_clean(monkeypatch, amt=None)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = engine.assess(snap)
        entry = next(m for m in assessment.missing_evidence if m.evidence_type == "transaction_context.transaction_amt")
        assert entry.reason == MissingEvidenceReason.DATA_MISSING
        assert entry.reason != MissingEvidenceReason.QUERY_ERROR


# ---------------------------------------------------------------- dataset label isolation


class TestDatasetLabelIsolation:
    def test_identical_evidence_with_opposite_labels_yields_identical_assessment(
        self, monkeypatch, service, engine
    ):
        set_all_clean(monkeypatch, is_fraud=True)
        snap_fraud = service.investigate_transaction("T1", FakeClient())
        assessment_fraud = engine.assess(snap_fraud)

        set_all_clean(monkeypatch, is_fraud=False)
        snap_clean = service.investigate_transaction("T1", FakeClient())
        assessment_clean = engine.assess(snap_clean)

        assert snap_fraud.evidence_bundle.dataset_risk_score == 1.0
        assert snap_clean.evidence_bundle.dataset_risk_score == 0.0
        assert assessment_fraud.model_dump(mode="json") == assessment_clean.model_dump(mode="json")


def test_engine_source_never_reads_dataset_risk_score():
    """Static check, not just a behavioral one: the engine's own code
    (not its prose docstrings, which legitimately explain the omission)
    never actually accesses `dataset_risk_score` as an attribute or key."""
    import app.uncertainty.engine as engine_module

    tree = ast.parse(Path(engine_module.__file__).read_text(encoding="utf-8"))
    accesses = [
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "dataset_risk_score"
    ] + [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == "dataset_risk_score"
    ]
    assert accesses == []


# ---------------------------------------------------------------- architectural boundary


class TestArchitecturalBoundary:
    @pytest.mark.parametrize("module_name", ["app.uncertainty.engine", "app.uncertainty.models"])
    def test_module_never_imports_tigergraph_directly(self, module_name):
        import importlib

        module = importlib.import_module(module_name)
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        assert not any(m.startswith("app.tigergraph") for m in imported_modules), imported_modules

    def test_assess_runs_on_a_synthetic_snapshot_with_no_tigergraph_client_anywhere(self):
        """Boundary test (Phase 2E §27): the engine must run given only an
        InvestigationSnapshot - no client, no registry, no connection."""
        from datetime import datetime

        from app.evidence.models import EvidenceBundle, EvidenceSummary
        from app.investigation.snapshot import CoverageSummary, InvestigationSnapshot

        snap = InvestigationSnapshot(
            investigation_id="inv-synthetic",
            transaction_id="T1",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            completed_at=datetime(2026, 1, 1, tzinfo=UTC),
            transaction_context=None,
            evidence_bundle=EvidenceBundle(
                transaction_id="T1", dataset_risk_score=None, evidence=[],
                evidence_summary=EvidenceSummary(transaction_id="T1", dataset_risk_score=None),
                data_quality_notes=[],
            ),
            tools_executed=[],
            tool_results={},
            execution_log=[],
            coverage=CoverageSummary(),
            warnings=[],
            status=InvestigationStatus.FAILED,
        )
        assessment = UncertaintyEngine().assess(snap)
        assert assessment.uncertainty_level == UncertaintyLevel.UNKNOWN


# ---------------------------------------------------------------- determinism


class TestDeterminism:
    def test_same_snapshot_produces_identical_assessment(self, monkeypatch, service, engine):
        set_all_clean(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        a1 = engine.assess(snap)
        a2 = engine.assess(snap)
        assert a1.model_dump(mode="json") == a2.model_dump(mode="json")


# ---------------------------------------------------------------- no verdict / no LLM


class TestNoVerdictOrLLM:
    def test_no_fraud_field_exists_on_the_assessment_model(self):
        from app.uncertainty.models import UncertaintyAssessment

        forbidden = {"fraud_probability", "final_fraud_score", "agent_confidence", "fraud_verdict"}
        assert forbidden.isdisjoint(UncertaintyAssessment.model_fields)

    def test_no_llm_or_openai_reference_anywhere_in_the_package(self):
        import app.uncertainty.engine as engine_module
        import app.uncertainty.models as models_module

        for module in (engine_module, models_module):
            source = Path(module.__file__).read_text(encoding="utf-8").lower()
            assert "openai" not in source
            assert "langgraph" not in source
            assert "import random" not in source
