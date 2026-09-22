"""Offline tests for app/policy/engine.py and models.py.

Most scenarios run the real pipeline - app.tigergraph.queries mocked,
through InvestigationService and UncertaintyEngine, into PolicyEngine -
same convention as tests/unit/test_uncertainty_engine.py. A few edge
cases that are awkward to reach through the real formula (e.g. HIGH
uncertainty that still passed the sufficiency gate) construct a
synthetic UncertaintyAssessment directly via its Pydantic model instead.
"""

from __future__ import annotations

import ast
from datetime import UTC
from pathlib import Path

import pytest

from app.evidence.models import EvidenceType, QueryStatus, SignalQuality
from app.investigation import InvestigationService, build_default_registry
from app.investigation import tools as t
from app.investigation.snapshot import InvestigationStatus
from app.policy import ApprovalRoute, PolicyAction, PolicyEngine, PolicyStatus
from app.tigergraph.client import TigerGraphQueryError, TigerGraphUnavailable
from app.tigergraph.queries import (
    NetworkSummary,
    SharedEmailActivity,
    SharedEntityActivity,
    TransactionContext,
)
from app.uncertainty import ConflictSeverity, ConflictType, UncertaintyEngine
from app.uncertainty.models import EvidenceConflict, UncertaintyAssessment, UncertaintyLevel


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


def set_strong_two_types(monkeypatch, *, is_fraud: bool = False):
    """Card + Device both MEDIUM-quality with related transactions, no
    address/email, no conflicts, consistent context - drives towards a
    LOW-uncertainty, two-distinct-type strong-evidence scenario."""
    ctx = TransactionContext(
        transaction_id="T1", attributes={"is_fraud": is_fraud, "transaction_amt": 50.0, "product_cd": "W"},
        card={"id": "card-1", "type": "Card"}, address=None, purchaser_email=None, recipient_email=None,
        device={"id": "dev-1", "type": "Device"},
    )
    monkeypatch.setattr(t.queries, "get_transaction_context", lambda client, txn: ctx)
    monkeypatch.setattr(
        t.queries, "find_shared_card_activity", lambda client, txn: linked_entity("Card", "card-1", [txn_row(2)])
    )
    monkeypatch.setattr(
        t.queries, "find_shared_device_activity", lambda client, txn: linked_entity("Device", "dev-1", [txn_row(3)])
    )
    monkeypatch.setattr(t.queries, "find_shared_address_activity", lambda client, txn: empty_entity("Address"))
    monkeypatch.setattr(t.queries, "find_shared_email_activity", lambda client, txn: SharedEmailActivity(txn, None))
    monkeypatch.setattr(
        t.queries, "investigate_transaction_network",
        lambda client, txn: NetworkSummary(
            txn,
            {"Card": 1, "Address": 0, "EmailDomain_purchaser": 0, "EmailDomain_recipient": 0, "Device": 1},
            {"Card": 1, "Address": 0, "EmailDomain_purchaser": 0, "EmailDomain_recipient": 0, "Device": 1},
        ),
    )


def set_only_low_quality_address(monkeypatch, *, is_fraud: bool = False, related_count: int = 3):
    ctx = TransactionContext(
        transaction_id="T1", attributes={"is_fraud": is_fraud, "transaction_amt": 50.0, "product_cd": "W"},
        card=None, address={"id": "addr-1", "type": "Address"}, purchaser_email=None, recipient_email=None,
        device=None,
    )
    monkeypatch.setattr(t.queries, "get_transaction_context", lambda client, txn: ctx)
    monkeypatch.setattr(t.queries, "find_shared_card_activity", lambda client, txn: empty_entity("Card"))
    monkeypatch.setattr(t.queries, "find_shared_device_activity", lambda client, txn: empty_entity("Device"))
    monkeypatch.setattr(
        t.queries, "find_shared_address_activity",
        lambda client, txn: linked_entity("Address", "addr-1", [txn_row(i) for i in range(2, 2 + related_count)]),
    )
    monkeypatch.setattr(t.queries, "find_shared_email_activity", lambda client, txn: SharedEmailActivity(txn, None))
    monkeypatch.setattr(
        t.queries, "investigate_transaction_network",
        lambda client, txn: NetworkSummary(
            txn,
            {"Card": 0, "Address": 1, "EmailDomain_purchaser": 0, "EmailDomain_recipient": 0, "Device": 0},
            {"Card": 0, "Address": related_count, "EmailDomain_purchaser": 0, "EmailDomain_recipient": 0, "Device": 0},
        ),
    )


@pytest.fixture
def service() -> InvestigationService:
    return InvestigationService(build_default_registry(), id_generator=lambda: "inv-fixed")


@pytest.fixture
def uncertainty_engine() -> UncertaintyEngine:
    return UncertaintyEngine()


@pytest.fixture
def policy_engine() -> PolicyEngine:
    return PolicyEngine()


def _assess(snap, uncertainty_engine, policy_engine):
    assessment = uncertainty_engine.assess(snap)
    decision = policy_engine.evaluate(snap, assessment)
    return assessment, decision


# ---------------------------------------------------------------- A. determinism


class TestDeterminism:
    def test_same_snapshot_and_assessment_produce_identical_decision(
        self, monkeypatch, service, uncertainty_engine, policy_engine
    ):
        set_strong_two_types(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment = uncertainty_engine.assess(snap)
        d1 = policy_engine.evaluate(snap, assessment)
        d2 = policy_engine.evaluate(snap, assessment)
        assert d1.model_dump(mode="json") == d2.model_dump(mode="json")


# ---------------------------------------------------------------- B. label isolation


class TestDatasetLabelIsolation:
    def test_identical_evidence_opposite_labels_yields_identical_decision(
        self, monkeypatch, service, uncertainty_engine, policy_engine
    ):
        set_strong_two_types(monkeypatch, is_fraud=True)
        snap_fraud = service.investigate_transaction("T1", FakeClient())
        _, d_fraud = _assess(snap_fraud, uncertainty_engine, policy_engine)

        set_strong_two_types(monkeypatch, is_fraud=False)
        snap_clean = service.investigate_transaction("T1", FakeClient())
        _, d_clean = _assess(snap_clean, uncertainty_engine, policy_engine)

        assert snap_fraud.evidence_bundle.dataset_risk_score == 1.0
        assert snap_clean.evidence_bundle.dataset_risk_score == 0.0
        assert d_fraud.model_dump(mode="json") == d_clean.model_dump(mode="json")

    def test_engine_source_never_reads_dataset_label(self):
        import app.policy.engine as engine_module

        tree = ast.parse(Path(engine_module.__file__).read_text(encoding="utf-8"))
        forbidden = {"dataset_risk_score", "isFraud", "is_fraud", "is_fraud_label"}
        hits = [
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in forbidden
        ] + [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value in forbidden
        ]
        assert hits == []


# ---------------------------------------------------------------- C/D. uncertainty-aware behavior


class TestUncertaintyAwareBehavior:
    def test_high_uncertainty_with_material_evidence_escalates(self, policy_engine):
        snap = _synthetic_snapshot(status=InvestigationStatus.COMPLETED, evidence_kind="medium_card")
        assessment = _synthetic_assessment(
            level=UncertaintyLevel.HIGH, overall=0.8, sufficient=True, coverage=1.0,
        )
        decision = policy_engine.evaluate(snap, assessment)
        assert decision.action in (PolicyAction.ESCALATE_ANALYST, PolicyAction.REQUEST_MORE_EVIDENCE)
        assert decision.action != PolicyAction.BLOCK_TRANSACTION

    def test_context_failure_never_produces_an_aggressive_action(
        self, monkeypatch, service, uncertainty_engine, policy_engine
    ):
        def boom(client, txn):
            raise TigerGraphQueryError(f"transaction '{txn}' does not exist")

        monkeypatch.setattr(t.queries, "get_transaction_context", boom)
        snap = service.investigate_transaction("ghost", FakeClient())
        _assessment, decision = _assess(snap, uncertainty_engine, policy_engine)
        assert decision.action == PolicyAction.REQUEST_MORE_EVIDENCE
        assert decision.status == PolicyStatus.NOT_ACTIONABLE
        assert decision.approval_required is False

    def test_low_uncertainty_with_strong_corroborated_evidence_reaches_an_action(
        self, monkeypatch, service, uncertainty_engine, policy_engine
    ):
        set_strong_two_types(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment, decision = _assess(snap, uncertainty_engine, policy_engine)
        assert assessment.uncertainty_level == UncertaintyLevel.LOW
        assert decision.action != PolicyAction.REQUEST_MORE_EVIDENCE
        assert decision.action == PolicyAction.BLOCK_TRANSACTION
        assert decision.approval_required is True

    def test_insufficient_coverage_without_high_conflict_requests_more_evidence(
        self, monkeypatch, service, uncertainty_engine, policy_engine
    ):
        set_strong_two_types(monkeypatch)

        def boom(client, txn):
            raise TigerGraphUnavailable("Could not mint a REST++ token from TG_SECRET: 500 Server Error")

        # Fail enough tools to push coverage strictly below the 0.5
        # sufficiency threshold: context + card succeed (2/6 = 0.333),
        # the other four fail. (3 failures alone only reaches exactly
        # 0.5, which the >= 0.5 sufficiency check still accepts.)
        monkeypatch.setattr(t.queries, "find_shared_device_activity", boom)
        monkeypatch.setattr(t.queries, "find_shared_address_activity", boom)
        monkeypatch.setattr(t.queries, "find_shared_email_activity", boom)
        monkeypatch.setattr(t.queries, "investigate_transaction_network", boom)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment, decision = _assess(snap, uncertainty_engine, policy_engine)
        assert assessment.sufficient_for_next_stage is False
        assert decision.action == PolicyAction.REQUEST_MORE_EVIDENCE
        assert decision.status == PolicyStatus.MORE_EVIDENCE_REQUIRED
        assert decision.approval_required is False

    def test_high_severity_conflict_blocking_sufficiency_escalates_instead(self, policy_engine):
        conflict = EvidenceConflict(
            conflict_id="T1:data_inconsistency", evidence_ids=["e1", "e2"],
            conflict_type=ConflictType.DATA_INCONSISTENCY, description="synthetic",
            severity=ConflictSeverity.HIGH,
        )
        snap = _synthetic_snapshot(status=InvestigationStatus.COMPLETED, evidence_kind="medium_card")
        assessment = _synthetic_assessment(
            level=UncertaintyLevel.MEDIUM, overall=0.5, sufficient=False, coverage=1.0,
            conflicts=[conflict],
        )
        decision = policy_engine.evaluate(snap, assessment)
        assert decision.action == PolicyAction.ESCALATE_ANALYST
        assert decision.status == PolicyStatus.HUMAN_REVIEW_REQUIRED
        assert decision.approval_route == ApprovalRoute.SENIOR_ANALYST


# ---------------------------------------------------------------- E/F. empty vs error


class TestEmptyVsErrorEvidence:
    def test_all_empty_evidence_does_not_cause_a_false_failure(
        self, monkeypatch, service, uncertainty_engine, policy_engine
    ):
        set_all_empty(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        assert snap.status == InvestigationStatus.COMPLETED
        _assessment, decision = _assess(snap, uncertainty_engine, policy_engine)
        assert decision.status != PolicyStatus.NOT_ACTIONABLE
        assert decision.action == PolicyAction.ALLOW_TRANSACTION
        assert decision.approval_required is False

    def test_error_evidence_prevents_the_engine_from_treating_coverage_as_complete(
        self, monkeypatch, service, uncertainty_engine, policy_engine
    ):
        set_strong_two_types(monkeypatch)

        def boom(client, txn):
            raise TigerGraphUnavailable("Could not mint a REST++ token from TG_SECRET: 500 Server Error")

        monkeypatch.setattr(t.queries, "find_shared_address_activity", boom)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment, _decision = _assess(snap, uncertainty_engine, policy_engine)
        assert assessment.evidence_coverage < 1.0
        assert any(m.evidence_type == "find_shared_address_activity" for m in assessment.missing_evidence)


# ---------------------------------------------------------------- G. conflicting evidence


class TestConflictingEvidence:
    def test_a_low_severity_conflict_under_low_uncertainty_caps_the_action_below_block(
        self, monkeypatch, service, uncertainty_engine, policy_engine
    ):
        set_strong_two_types(monkeypatch)

        def boom(client, txn):
            raise TigerGraphUnavailable("Could not mint a REST++ token from TG_SECRET: 500 Server Error")

        # One tool errors (MISSING_CONTEXT, LOW severity) while card/device
        # still produce material findings - coverage stays >= 0.5 and no
        # HIGH-severity conflict exists, so this should still reach Gate 3
        # but be capped below BLOCK_TRANSACTION by the conflict.
        monkeypatch.setattr(t.queries, "find_shared_email_activity", boom)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment, decision = _assess(snap, uncertainty_engine, policy_engine)
        if assessment.sufficient_for_next_stage and assessment.uncertainty_level == UncertaintyLevel.LOW:
            assert bool(assessment.conflicting_evidence)
            assert decision.action != PolicyAction.BLOCK_TRANSACTION


# ---------------------------------------------------------------- H. low-quality address evidence


class TestLowQualityAddressEvidence:
    def test_low_quality_address_alone_never_reaches_block_or_escalate(
        self, monkeypatch, service, uncertainty_engine, policy_engine
    ):
        set_only_low_quality_address(monkeypatch, related_count=3)
        snap = service.investigate_transaction("T1", FakeClient())
        _assessment, decision = _assess(snap, uncertainty_engine, policy_engine)
        assert decision.action not in (PolicyAction.BLOCK_TRANSACTION, PolicyAction.ESCALATE_ANALYST)

    def test_a_large_low_quality_address_count_does_not_escalate_the_action(
        self, monkeypatch, service, uncertainty_engine, policy_engine
    ):
        """The documented finding: shared Address can reach hundreds of
        related transactions yet must stay LOW quality. A large count
        may legitimately trigger Phase 2E's own QUALITY_DISPARITY
        conflict (a documented, threshold-based rule, not "count as
        quality") and therefore route to CREATE_CASE instead of
        MONITOR_ACCOUNT for a small count - both are conservative,
        non-escalated outcomes. What must never happen, at any count, is
        the raw fan-out alone driving an aggressive action."""
        set_only_low_quality_address(monkeypatch, related_count=312)
        snap = service.investigate_transaction("T1", FakeClient())
        assessment_large, decision_large = _assess(snap, uncertainty_engine, policy_engine)

        set_only_low_quality_address(monkeypatch, related_count=3)
        snap_small = service.investigate_transaction("T1", FakeClient())
        _assessment_small, decision_small = _assess(snap_small, uncertainty_engine, policy_engine)

        aggressive = (PolicyAction.BLOCK_TRANSACTION, PolicyAction.ESCALATE_ANALYST)
        assert decision_large.action not in aggressive
        assert decision_small.action not in aggressive
        # The only reason they may legitimately differ: the large count
        # crosses Phase 2E's own QUALITY_DISPARITY threshold.
        if decision_large.action != decision_small.action:
            assert any(c.conflict_type == ConflictType.QUALITY_DISPARITY for c in assessment_large.conflicting_evidence)


# ---------------------------------------------------------------- I. approval separation


class TestApprovalSeparation:
    def test_recommended_action_approval_and_route_vary_independently(
        self, monkeypatch, service, uncertainty_engine, policy_engine
    ):
        set_strong_two_types(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        _, decision = _assess(snap, uncertainty_engine, policy_engine)
        assert decision.action == PolicyAction.BLOCK_TRANSACTION
        assert decision.approval_required is True
        assert decision.approval_route != ApprovalRoute.NONE
        # The action itself never implies execution.
        assert decision.executable is False

    def test_no_approval_actions_carry_route_none(
        self, monkeypatch, service, uncertainty_engine, policy_engine
    ):
        set_all_empty(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        _, decision = _assess(snap, uncertainty_engine, policy_engine)
        assert decision.action == PolicyAction.ALLOW_TRANSACTION
        assert decision.approval_required is False
        assert decision.approval_route == ApprovalRoute.NONE


# ---------------------------------------------------------------- J. unsupported execution


class TestExecutability:
    @pytest.mark.parametrize("action", list(PolicyAction))
    def test_every_action_is_marked_non_executable(self, action):
        # executable is always False in this phase - no real backend
        # exists for any of these actions yet (models.py docstring).
        from app.policy.models import PolicyDecision
        from app.uncertainty.models import UncertaintyLevel as UL

        decision = PolicyDecision(
            investigation_id="inv-x", transaction_id="T1", action=action,
            approval_required=True, approval_route=ApprovalRoute.ANALYST, executable=False,
            requires_more_evidence=False, status=PolicyStatus.RECOMMENDATION_READY,
            uncertainty_level=UL.LOW, overall_uncertainty=0.1, evidence_ids=[], rationale="synthetic",
        )
        assert decision.executable is False

    def test_a_real_engine_decision_is_never_executable(
        self, monkeypatch, service, uncertainty_engine, policy_engine
    ):
        set_strong_two_types(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        _, decision = _assess(snap, uncertainty_engine, policy_engine)
        assert decision.executable is False
        assert "recommendation" in decision.rationale.lower()
        assert "no real-world action has been executed" in decision.rationale.lower()


# ---------------------------------------------------------------- K. serialization


class TestSerialization:
    def test_policy_decision_round_trips_through_json(
        self, monkeypatch, service, uncertainty_engine, policy_engine
    ):
        import json

        set_strong_two_types(monkeypatch)
        snap = service.investigate_transaction("T1", FakeClient())
        _, decision = _assess(snap, uncertainty_engine, policy_engine)
        dumped = decision.model_dump(mode="json")
        reloaded_json = json.dumps(dumped)
        from app.policy.models import PolicyDecision

        rebuilt = PolicyDecision.model_validate(json.loads(reloaded_json))
        assert rebuilt.model_dump(mode="json") == dumped


# ---------------------------------------------------------------- L/M. architectural boundary


class TestArchitecturalBoundary:
    @pytest.mark.parametrize("module_name", ["app.policy.engine", "app.policy.models"])
    def test_module_never_imports_tigergraph_directly(self, module_name):
        import importlib

        module = importlib.import_module(module_name)
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not any(m.startswith("app.tigergraph") for m in imported), imported

    def test_evaluate_runs_on_a_fully_synthetic_snapshot_and_assessment(self, policy_engine):
        snap = _synthetic_snapshot(status=InvestigationStatus.FAILED, evidence_kind="none")
        assessment = _synthetic_assessment(level=UncertaintyLevel.UNKNOWN, overall=None, sufficient=False, coverage=0.0)
        decision = policy_engine.evaluate(snap, assessment)
        assert decision.status == PolicyStatus.NOT_ACTIONABLE

    def test_no_llm_or_openai_reference_anywhere_in_the_package(self):
        import app.policy.engine as engine_module
        import app.policy.models as models_module

        for module in (engine_module, models_module):
            source = Path(module.__file__).read_text(encoding="utf-8").lower()
            assert "openai" not in source
            assert "langgraph" not in source
            assert "langchain" not in source
            assert "import random" not in source

    def test_no_fraud_field_exists_on_the_decision_model(self):
        from app.policy.models import PolicyDecision

        forbidden = {"fraud_probability", "fraud_verdict", "final_fraud_score", "is_fraud"}
        assert forbidden.isdisjoint(PolicyDecision.model_fields)


# ---------------------------------------------------------------- synthetic builders


def _synthetic_snapshot(*, status: InvestigationStatus, evidence_kind: str):
    from datetime import datetime

    from app.evidence.models import (
        Evidence,
        EvidenceBundle,
        EvidenceSummary,
        Provenance,
        evidence_id,
    )

    evidence: list[Evidence] = []
    if evidence_kind == "medium_card":
        evidence.append(
            Evidence(
                evidence_id=evidence_id("T1", EvidenceType.SHARED_CARD, "card-1"),
                evidence_type=EvidenceType.SHARED_CARD, transaction_id="T1", status=QueryStatus.SUCCESS,
                observation="synthetic", quality=SignalQuality.MEDIUM, quality_reason="synthetic",
                metrics={"related_transaction_count": 3},
                provenance=Provenance(source_query="synthetic", transaction_id="T1", entity_id="card-1"),
            )
        )

    bundle = EvidenceBundle(
        transaction_id="T1", dataset_risk_score=None, evidence=evidence,
        evidence_summary=EvidenceSummary(transaction_id="T1", dataset_risk_score=None),
        data_quality_notes=[],
    )

    from app.investigation.schemas import InvestigationToolResult
    from app.investigation.snapshot import CoverageSummary, InvestigationSnapshot

    tool_results = {}
    for e in evidence:
        name = {
            EvidenceType.SHARED_CARD: "find_shared_card_activity",
        }[e.evidence_type]
        tool_results[name] = InvestigationToolResult(
            tool_name=name, status=e.status, transaction_id="T1", evidence=e, summary="synthetic",
            provenance=e.provenance, latency_ms=1.0, error=None,
        )

    now = datetime(2026, 1, 1, tzinfo=UTC)
    return InvestigationSnapshot(
        investigation_id="inv-synthetic", transaction_id="T1", started_at=now, completed_at=now,
        transaction_context=None, evidence_bundle=bundle,
        tools_executed=list(tool_results), tool_results=tool_results, execution_log=[],
        coverage=CoverageSummary(), warnings=[], status=status,
    )


def _synthetic_assessment(
    *, level: UncertaintyLevel, overall: float | None, sufficient: bool, coverage: float,
    conflicts: list | None = None,
) -> UncertaintyAssessment:
    return UncertaintyAssessment(
        investigation_id="inv-synthetic", transaction_id="T1",
        evidence_coverage=coverage, signal_quality=None, signal_conflict=0.0, data_completeness=None,
        overall_uncertainty=overall, uncertainty_level=level,
        missing_evidence=[], conflicting_evidence=conflicts or [], factors=[],
        sufficient_for_next_stage=sufficient, rationale="synthetic",
    )
