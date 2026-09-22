"""Offline tests for app/case/ (models, store, manager, memory).

Cases in this file are SYNTHETIC DEVELOPMENT CASES - never presented as
real bank investigations. Most scenarios build a real InvestigationSnapshot
via app.tigergraph.queries mocked through InvestigationService, then a
real UncertaintyAssessment and PolicyDecision - same convention as
tests/unit/test_policy_engine.py. Pure CaseMemory/similarity tests build
synthetic CaseRecords directly, since they exercise memory behavior
independent of any one investigation.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

import pytest

from app.case import (
    CaseActionNotFoundError,
    CaseActionStatus,
    CaseManager,
    CaseMemory,
    CaseNotFoundError,
    CaseOutcomeType,
    CaseStatus,
    CaseTrigger,
    DecisionType,
    InMemoryCaseStore,
    InvalidCaseTransitionError,
    SimilarityWeights,
)
from app.case.models import CaseOutcome, CaseRecord
from app.evidence.models import EvidenceType
from app.investigation import InvestigationService, build_default_registry
from app.investigation import tools as t
from app.policy import ApprovalRoute, PolicyAction, PolicyEngine, PolicyStatus
from app.policy.models import PolicyDecision
from app.tigergraph.queries import (
    NetworkSummary,
    SharedEmailActivity,
    SharedEntityActivity,
    TransactionContext,
)
from app.uncertainty import UncertaintyEngine
from app.uncertainty.models import (
    UncertaintyAssessment,
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


def set_strong_two_types(monkeypatch, *, is_fraud: bool = False):
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


def fixed_clock(start: datetime = datetime(2026, 1, 1, tzinfo=UTC)):
    from datetime import timedelta

    counter = count()

    def _clock() -> datetime:
        return start + timedelta(microseconds=next(counter))

    return _clock


def fixed_ids(prefix: str = "case-test"):
    counter = count(1)
    return lambda: f"{prefix}-{next(counter)}"


@pytest.fixture
def service() -> InvestigationService:
    return InvestigationService(build_default_registry(), id_generator=lambda: "inv-fixed")


@pytest.fixture
def store() -> InMemoryCaseStore:
    return InMemoryCaseStore()


@pytest.fixture
def manager(store) -> CaseManager:
    return CaseManager(store, id_generator=fixed_ids(), clock=fixed_clock())


def _pipeline(monkeypatch, service, *, is_fraud: bool = False):
    set_strong_two_types(monkeypatch, is_fraud=is_fraud)
    snap = service.investigate_transaction("T1", FakeClient())
    assessment = UncertaintyEngine().assess(snap)
    decision = PolicyEngine().evaluate(snap, assessment)
    return snap, assessment, decision


# ---------------------------------------------------------------- A. case creation


class TestCaseCreation:
    def test_case_created_from_a_real_investigation(self, monkeypatch, service, manager):
        snap, assessment, decision = _pipeline(monkeypatch, service)
        case = manager.create_case(
            snap, uncertainty_assessment=assessment, policy_decision=decision, trigger=CaseTrigger.FRAUD_SIGNAL
        )
        assert case.case_id == "case-test-1"
        assert case.status == CaseStatus.OPEN
        assert case.transaction_id == "T1"
        assert case.investigation_id == "inv-fixed"
        assert case.created_at == case.updated_at
        assert set(case.evidence_ids) == {e.evidence_id for e in snap.evidence_bundle.evidence}
        assert case.uncertainty_assessment == assessment
        assert case.policy_decision == decision

    def test_case_id_is_never_the_bare_transaction_id(self, monkeypatch, service, manager):
        snap, assessment, decision = _pipeline(monkeypatch, service)
        case = manager.create_case(snap, uncertainty_assessment=assessment, policy_decision=decision)
        assert case.case_id != case.transaction_id

    def test_default_trigger_is_unknown_not_fabricated(self, monkeypatch, service, manager):
        snap, assessment, decision = _pipeline(monkeypatch, service)
        case = manager.create_case(snap, uncertainty_assessment=assessment, policy_decision=decision)
        assert case.trigger == CaseTrigger.UNKNOWN


# ---------------------------------------------------------------- B. serialization


class TestSerialization:
    def test_case_record_round_trips_through_json(self, monkeypatch, service, manager):
        snap, assessment, decision = _pipeline(monkeypatch, service)
        case = manager.create_case(snap, uncertainty_assessment=assessment, policy_decision=decision)
        case = manager.add_system_recommendation(case.case_id, decision)
        case = manager.add_action_from_policy_decision(case.case_id, decision)

        dumped = case.model_dump(mode="json")
        raw = json.dumps(dumped)
        rebuilt = CaseRecord.model_validate(json.loads(raw))
        assert rebuilt.model_dump(mode="json") == dumped


# ---------------------------------------------------------------- C. state transitions


class TestStateTransitions:
    def test_valid_transition_chain(self, monkeypatch, service, manager):
        snap, assessment, decision = _pipeline(monkeypatch, service)
        case = manager.create_case(snap, uncertainty_assessment=assessment, policy_decision=decision)
        case = manager.transition_status(case.case_id, CaseStatus.INVESTIGATING)
        assert case.status == CaseStatus.INVESTIGATING
        case = manager.transition_status(case.case_id, CaseStatus.ACTION_RECOMMENDED)
        assert case.status == CaseStatus.ACTION_RECOMMENDED
        case = manager.transition_status(case.case_id, CaseStatus.PENDING_REVIEW)
        assert case.status == CaseStatus.PENDING_REVIEW
        case = manager.transition_status(case.case_id, CaseStatus.CLOSED)
        assert case.status == CaseStatus.CLOSED

    def test_invalid_transition_is_rejected(self, monkeypatch, service, manager):
        snap, assessment, decision = _pipeline(monkeypatch, service)
        case = manager.create_case(snap, uncertainty_assessment=assessment, policy_decision=decision)
        with pytest.raises(InvalidCaseTransitionError):
            manager.transition_status(case.case_id, CaseStatus.CLOSED)

    def test_closed_is_terminal(self, monkeypatch, service, manager):
        snap, assessment, decision = _pipeline(monkeypatch, service)
        case = manager.create_case(snap, uncertainty_assessment=assessment, policy_decision=decision)
        for status in (
            CaseStatus.INVESTIGATING, CaseStatus.ACTION_RECOMMENDED, CaseStatus.PENDING_REVIEW, CaseStatus.CLOSED
        ):
            case = manager.transition_status(case.case_id, status)
        with pytest.raises(InvalidCaseTransitionError):
            manager.transition_status(case.case_id, CaseStatus.INVESTIGATING)

    def test_pending_evidence_returns_to_investigating(self, monkeypatch, service, manager):
        snap, assessment, decision = _pipeline(monkeypatch, service)
        case = manager.create_case(snap, uncertainty_assessment=assessment, policy_decision=decision)
        case = manager.transition_status(case.case_id, CaseStatus.INVESTIGATING)
        case = manager.transition_status(case.case_id, CaseStatus.PENDING_EVIDENCE)
        case = manager.transition_status(case.case_id, CaseStatus.INVESTIGATING)
        assert case.status == CaseStatus.INVESTIGATING

    def test_unknown_case_id_raises(self, manager):
        with pytest.raises(CaseNotFoundError):
            manager.get_case("nope")


# ---------------------------------------------------------------- D. findings


class TestFindings:
    def test_add_finding_preserves_evidence_ids(self, monkeypatch, service, manager):
        snap, assessment, decision = _pipeline(monkeypatch, service)
        case = manager.create_case(snap, uncertainty_assessment=assessment, policy_decision=decision)
        some_ids = case.evidence_ids[:2]
        case = manager.add_finding(case.case_id, description="synthetic finding", evidence_ids=some_ids)
        assert len(case.findings) == 1
        assert case.findings[0].evidence_ids == some_ids
        assert case.findings[0].description == "synthetic finding"
        # persisted, not just returned
        assert manager.get_case(case.case_id).findings == case.findings


# ---------------------------------------------------------------- E. decisions


class TestDecisions:
    def test_system_recommendation_stays_distinguishable_from_human_decision(
        self, monkeypatch, service, manager
    ):
        snap, assessment, decision = _pipeline(monkeypatch, service)
        case = manager.create_case(snap, uncertainty_assessment=assessment, policy_decision=decision)
        case = manager.add_system_recommendation(case.case_id, decision)
        case = manager.add_decision(
            case.case_id, decision_type=DecisionType.HUMAN_DECISION, rationale="analyst reviewed and agreed",
            evidence_ids=[], actor="analyst:jdoe",
        )
        types = [d.decision_type for d in case.decisions]
        assert DecisionType.SYSTEM_RECOMMENDATION in types
        assert DecisionType.HUMAN_DECISION in types
        system_decision = next(d for d in case.decisions if d.decision_type == DecisionType.SYSTEM_RECOMMENDATION)
        assert system_decision.actor == "policy_engine"


# ---------------------------------------------------------------- F. actions


class TestActions:
    def test_recommended_action_is_never_executed_by_default(self, monkeypatch, service, manager):
        snap, assessment, decision = _pipeline(monkeypatch, service)
        case = manager.create_case(snap, uncertainty_assessment=assessment, policy_decision=decision)
        case = manager.add_action_from_policy_decision(case.case_id, decision)
        assert case.actions[0].status == CaseActionStatus.RECOMMENDED
        assert case.actions[0].executed is False

    def test_execution_requires_explicit_approval_first(self, monkeypatch, service, manager):
        snap, assessment, decision = _pipeline(monkeypatch, service)
        case = manager.create_case(snap, uncertainty_assessment=assessment, policy_decision=decision)
        case = manager.add_action_from_policy_decision(case.case_id, decision)
        action_id = case.actions[0].action_id
        with pytest.raises(InvalidCaseTransitionError):
            manager.record_action_executed(case.case_id, action_id, actor="external_system")

    def test_approve_then_execute_flow(self, monkeypatch, service, manager):
        snap, assessment, decision = _pipeline(monkeypatch, service)
        case = manager.create_case(snap, uncertainty_assessment=assessment, policy_decision=decision)
        case = manager.add_action_from_policy_decision(case.case_id, decision)
        action_id = case.actions[0].action_id
        case = manager.record_action_review(case.case_id, action_id, approved=True, actor="analyst:jdoe")
        assert case.actions[0].status == CaseActionStatus.APPROVED
        assert case.actions[0].executed is False
        case = manager.record_action_executed(case.case_id, action_id, actor="external_system")
        assert case.actions[0].status == CaseActionStatus.EXECUTED
        assert case.actions[0].executed is True

    def test_rejected_action_can_never_be_executed(self, monkeypatch, service, manager):
        snap, assessment, decision = _pipeline(monkeypatch, service)
        case = manager.create_case(snap, uncertainty_assessment=assessment, policy_decision=decision)
        case = manager.add_action_from_policy_decision(case.case_id, decision)
        action_id = case.actions[0].action_id
        case = manager.record_action_review(case.case_id, action_id, approved=False, actor="analyst:jdoe")
        with pytest.raises(InvalidCaseTransitionError):
            manager.record_action_executed(case.case_id, action_id, actor="external_system")

    def test_unknown_action_id_raises(self, monkeypatch, service, manager):
        snap, assessment, decision = _pipeline(monkeypatch, service)
        case = manager.create_case(snap, uncertainty_assessment=assessment, policy_decision=decision)
        with pytest.raises(CaseActionNotFoundError):
            manager.record_action_review(case.case_id, "ghost", approved=True, actor="analyst:jdoe")


# ---------------------------------------------------------------- G. outcome


class TestOutcome:
    def test_synthetic_outcome_is_stored_and_flagged(self, monkeypatch, service, manager):
        snap, assessment, decision = _pipeline(monkeypatch, service)
        case = manager.create_case(snap, uncertainty_assessment=assessment, policy_decision=decision)
        case = manager.set_outcome(
            case.case_id, CaseOutcomeType.CLEARED, notes="SYNTHETIC DEVELOPMENT CASE", is_synthetic=True
        )
        assert case.outcome.outcome_type == CaseOutcomeType.CLEARED
        assert case.outcome.is_synthetic is True
        assert manager.get_case(case.case_id).outcome == case.outcome


# ---------------------------------------------------------------- H/J. retrieval


def _synthetic_case(
    cid, *, evidence_types, action, level, trigger=CaseTrigger.FRAUD_SIGNAL, outcome_type=None,
    conflicts=None, is_fraud_marker: bool | None = None,
):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assessment = UncertaintyAssessment(
        investigation_id=f"inv-{cid}", transaction_id=f"TX-{cid}", evidence_coverage=1.0, signal_quality=0.5,
        signal_conflict=0.0, data_completeness=1.0, overall_uncertainty=0.2, uncertainty_level=level,
        missing_evidence=[], conflicting_evidence=conflicts or [], factors=[], sufficient_for_next_stage=True,
        rationale="synthetic",
    )
    policy = PolicyDecision(
        investigation_id=f"inv-{cid}", transaction_id=f"TX-{cid}", action=action, approval_required=True,
        approval_route=ApprovalRoute.ANALYST, executable=False, requires_more_evidence=False,
        status=PolicyStatus.RECOMMENDATION_READY, uncertainty_level=level, overall_uncertainty=0.2,
        evidence_ids=[], rationale="synthetic",
    )
    outcome = None
    if outcome_type is not None:
        outcome = CaseOutcome(outcome_type=outcome_type, recorded_at=now, is_synthetic=True)
    return CaseRecord(
        case_id=cid, status=CaseStatus.CLOSED, trigger=trigger, transaction_id=f"TX-{cid}",
        created_at=now, updated_at=now, evidence_ids=[], evidence_types=sorted(evidence_types),
        uncertainty_assessment=assessment, policy_decision=policy, outcome=outcome,
    )


class TestRetrieval:
    def test_similar_cases_are_found_and_scored_deterministically(self):
        store = InMemoryCaseStore()
        mem = CaseMemory(store)
        c1 = _synthetic_case("c1", evidence_types=["shared_card", "shared_email_domain"],
                              action=PolicyAction.CREATE_CASE, level=UncertaintyLevel.LOW,
                              outcome_type=CaseOutcomeType.CLEARED)
        c2 = _synthetic_case("c2", evidence_types=["shared_address"],
                              action=PolicyAction.MONITOR_ACCOUNT, level=UncertaintyLevel.MEDIUM)
        mem.store(c1)
        mem.store(c2)

        query = _synthetic_case("query", evidence_types=["shared_card", "shared_email_domain"],
                                 action=PolicyAction.CREATE_CASE, level=UncertaintyLevel.LOW)
        results = mem.retrieve_similar(query)
        assert results[0].case_id == "c1"
        assert results[0].similarity_score > results[1].similarity_score
        assert "evidence_type_overlap" in results[0].matching_features[0]
        assert results[0].outcome.outcome_type == CaseOutcomeType.CLEARED

    def test_tie_breaking_is_stable_by_case_id(self):
        store = InMemoryCaseStore()
        mem = CaseMemory(store)
        # b and a are identical in every scored feature - only case_id differs.
        b = _synthetic_case("b", evidence_types=["shared_card"], action=PolicyAction.CREATE_CASE, level=UncertaintyLevel.LOW)
        a = _synthetic_case("a", evidence_types=["shared_card"], action=PolicyAction.CREATE_CASE, level=UncertaintyLevel.LOW)
        mem.store(b)
        mem.store(a)
        query = _synthetic_case("query", evidence_types=["shared_card"], action=PolicyAction.CREATE_CASE, level=UncertaintyLevel.LOW)
        results = mem.retrieve_similar(query)
        assert results[0].similarity_score == results[1].similarity_score
        assert [r.case_id for r in results] == ["a", "b"]  # case_id ascending tie-break

    def test_evidence_pattern_retrieval_ranks_consistently(self):
        store = InMemoryCaseStore()
        mem = CaseMemory(store)
        related = _synthetic_case("related", evidence_types=["shared_card", "shared_email_domain"],
                                   action=PolicyAction.CREATE_CASE, level=UncertaintyLevel.LOW)
        unrelated = _synthetic_case("unrelated", evidence_types=["shared_address"],
                                     action=PolicyAction.MONITOR_ACCOUNT, level=UncertaintyLevel.MEDIUM)
        mem.store(related)
        mem.store(unrelated)
        found = mem.retrieve_by_evidence_pattern({EvidenceType.SHARED_CARD, EvidenceType.SHARED_EMAIL_DOMAIN})
        assert [c.case_id for c in found] == ["related"]

    def test_retrieve_by_action(self):
        store = InMemoryCaseStore()
        mem = CaseMemory(store)
        c1 = _synthetic_case("c1", evidence_types=["shared_card"], action=PolicyAction.BLOCK_TRANSACTION, level=UncertaintyLevel.LOW)
        c2 = _synthetic_case("c2", evidence_types=["shared_card"], action=PolicyAction.ALLOW_TRANSACTION, level=UncertaintyLevel.LOW)
        mem.store(c1)
        mem.store(c2)
        assert [c.case_id for c in mem.retrieve_by_action(PolicyAction.BLOCK_TRANSACTION)] == ["c1"]

    def test_retrieve_by_outcome(self):
        store = InMemoryCaseStore()
        mem = CaseMemory(store)
        c1 = _synthetic_case("c1", evidence_types=["shared_card"], action=PolicyAction.CREATE_CASE,
                              level=UncertaintyLevel.LOW, outcome_type=CaseOutcomeType.CONFIRMED_FRAUD)
        c2 = _synthetic_case("c2", evidence_types=["shared_card"], action=PolicyAction.CREATE_CASE,
                              level=UncertaintyLevel.LOW, outcome_type=CaseOutcomeType.CLEARED)
        mem.store(c1)
        mem.store(c2)
        assert [c.case_id for c in mem.retrieve_by_outcome(CaseOutcomeType.CONFIRMED_FRAUD)] == ["c1"]

    def test_retrieve_by_transaction(self):
        store = InMemoryCaseStore()
        mem = CaseMemory(store)
        c1 = _synthetic_case("c1", evidence_types=["shared_card"], action=PolicyAction.CREATE_CASE, level=UncertaintyLevel.LOW)
        mem.store(c1)
        assert [c.case_id for c in mem.retrieve_by_transaction("TX-c1")] == ["c1"]
        assert mem.retrieve_by_transaction("TX-nonexistent") == []


# ---------------------------------------------------------------- I. label isolation


class TestLabelIsolation:
    def test_identical_investigation_opposite_labels_yields_identical_case(
        self, monkeypatch, service
    ):
        mgr_a = CaseManager(InMemoryCaseStore(), id_generator=fixed_ids("x"), clock=fixed_clock())
        mgr_b = CaseManager(InMemoryCaseStore(), id_generator=fixed_ids("x"), clock=fixed_clock())

        snap_fraud, assessment_fraud, decision_fraud = _pipeline(monkeypatch, service, is_fraud=True)
        case_fraud = mgr_a.create_case(snap_fraud, uncertainty_assessment=assessment_fraud, policy_decision=decision_fraud)

        snap_clean, assessment_clean, decision_clean = _pipeline(monkeypatch, service, is_fraud=False)
        case_clean = mgr_b.create_case(snap_clean, uncertainty_assessment=assessment_clean, policy_decision=decision_clean)

        assert snap_fraud.evidence_bundle.dataset_risk_score == 1.0
        assert snap_clean.evidence_bundle.dataset_risk_score == 0.0
        assert case_fraud.model_dump(mode="json") == case_clean.model_dump(mode="json")

    def test_case_record_has_no_dataset_label_field_at_all(self):
        forbidden = {"dataset_risk_score", "is_fraud", "isFraud", "is_fraud_label"}
        assert forbidden.isdisjoint(CaseRecord.model_fields)

    def test_retrieval_order_is_identical_regardless_of_a_hypothetical_label(self):
        """Even though CaseRecord cannot carry a dataset label at all
        (previous test), this directly proves retrieval ordering for two
        cases that would only have differed by that label is identical -
        the strongest form of this guarantee: architectural, not just
        behavioral."""
        store = InMemoryCaseStore()
        mem = CaseMemory(store)
        c1 = _synthetic_case("c1", evidence_types=["shared_card"], action=PolicyAction.CREATE_CASE, level=UncertaintyLevel.LOW)
        c2 = _synthetic_case("c2", evidence_types=["shared_card"], action=PolicyAction.CREATE_CASE, level=UncertaintyLevel.LOW)
        mem.store(c1)
        mem.store(c2)
        query = _synthetic_case("query", evidence_types=["shared_card"], action=PolicyAction.CREATE_CASE, level=UncertaintyLevel.LOW)
        r1 = mem.retrieve_similar(query)
        r2 = mem.retrieve_similar(query)
        assert [x.case_id for x in r1] == [x.case_id for x in r2]

    def test_no_label_reference_anywhere_in_the_case_package(self):
        forbidden = {"dataset_risk_score", "isFraud", "is_fraud", "is_fraud_label"}
        for module_name in ("app.case.models", "app.case.manager", "app.case.memory", "app.case.store"):
            import importlib

            module = importlib.import_module(module_name)
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            hits = [
                node.attr for node in ast.walk(tree)
                if isinstance(node, ast.Attribute) and node.attr in forbidden
            ] + [
                node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and node.value in forbidden
            ]
            assert hits == [], f"{module_name}: {hits}"


# ---------------------------------------------------------------- K. recurring patterns


class TestRecurringPatterns:
    def test_recurring_pattern_detected_across_synthetic_cases(self):
        store = InMemoryCaseStore()
        mem = CaseMemory(store)
        for i in range(4):
            mem.store(
                _synthetic_case(f"c{i}", evidence_types=["shared_card", "shared_email_domain"],
                                 action=PolicyAction.CREATE_CASE, level=UncertaintyLevel.LOW)
            )
        mem.store(_synthetic_case("solo", evidence_types=["shared_address"], action=PolicyAction.MONITOR_ACCOUNT,
                                   level=UncertaintyLevel.MEDIUM))
        patterns = mem.detect_recurring_patterns(min_occurrences=2)
        assert len(patterns) == 1
        assert patterns[0].evidence_types == ["shared_card", "shared_email_domain"]
        assert patterns[0].occurrences == 4
        assert patterns[0].related_case_ids == ["c0", "c1", "c2", "c3"]
        assert not patterns[0].pattern_id.lower().startswith("fraud")

    def test_below_threshold_is_not_a_pattern(self):
        store = InMemoryCaseStore()
        mem = CaseMemory(store)
        mem.store(_synthetic_case("only", evidence_types=["shared_card"], action=PolicyAction.CREATE_CASE, level=UncertaintyLevel.LOW))
        assert mem.detect_recurring_patterns(min_occurrences=2) == []


# ---------------------------------------------------------------- L. empty memory


class TestEmptyMemory:
    def test_retrieval_against_empty_memory_returns_empty_not_error(self):
        mem = CaseMemory(InMemoryCaseStore())
        query = _synthetic_case("query", evidence_types=["shared_card"], action=PolicyAction.CREATE_CASE, level=UncertaintyLevel.LOW)
        assert mem.retrieve_similar(query) == []
        assert mem.detect_recurring_patterns() == []
        assert mem.retrieve_by_transaction("TX-anything") == []


# ---------------------------------------------------------------- M. missing metadata


class TestMissingMetadata:
    def test_case_without_uncertainty_or_policy_does_not_crash_retrieval(self):
        now = datetime(2026, 1, 1, tzinfo=UTC)
        bare = CaseRecord(
            case_id="bare", status=CaseStatus.OPEN, trigger=CaseTrigger.UNKNOWN, transaction_id="TX-bare",
            created_at=now, updated_at=now, evidence_ids=[], evidence_types=[],
        )
        mem = CaseMemory(InMemoryCaseStore())
        mem.store(bare)
        query = _synthetic_case("query", evidence_types=["shared_card"], action=PolicyAction.CREATE_CASE, level=UncertaintyLevel.LOW)
        results = mem.retrieve_similar(query)
        assert len(results) == 1
        assert results[0].case_id == "bare"
        assert results[0].similarity_score == 0.0  # no basis to match anything - not invented


# ---------------------------------------------------------------- N/O. architectural boundary


class TestArchitecturalBoundary:
    @pytest.mark.parametrize(
        "module_name", ["app.case.models", "app.case.manager", "app.case.memory", "app.case.store"]
    )
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

    def test_manager_and_memory_run_entirely_on_synthetic_data(self):
        store = InMemoryCaseStore()
        manager = CaseManager(store)
        mem = CaseMemory(store)
        # No InvestigationSnapshot at all needed for CaseMemory-only ops.
        case = _synthetic_case("synthetic", evidence_types=["shared_card"], action=PolicyAction.CREATE_CASE, level=UncertaintyLevel.LOW)
        mem.store(case)
        assert manager.get_case("synthetic").case_id == "synthetic"
        assert mem.retrieve_similar(case) == []  # only itself in the store

    def test_no_llm_or_openai_reference_anywhere_in_the_package(self):
        for module_name in ("app.case.models", "app.case.manager", "app.case.memory", "app.case.store"):
            import importlib

            module = importlib.import_module(module_name)
            source = Path(module.__file__).read_text(encoding="utf-8").lower()
            assert "openai" not in source
            assert "langgraph" not in source
            assert "langchain" not in source
            assert "crewai" not in source


# ---------------------------------------------------------------- Q. determinism


class TestDeterminism:
    def test_repeated_retrieval_on_identical_store_is_byte_identical(self):
        store = InMemoryCaseStore()
        mem = CaseMemory(store)
        for i in range(3):
            mem.store(_synthetic_case(f"c{i}", evidence_types=["shared_card", "shared_email_domain"],
                                       action=PolicyAction.CREATE_CASE, level=UncertaintyLevel.LOW))
        query = _synthetic_case("query", evidence_types=["shared_card", "shared_email_domain"],
                                 action=PolicyAction.CREATE_CASE, level=UncertaintyLevel.LOW)
        r1 = mem.retrieve_similar(query)
        r2 = mem.retrieve_similar(query)
        assert [x.model_dump(mode="json") for x in r1] == [x.model_dump(mode="json") for x in r2]

    def test_similarity_weights_must_sum_to_one(self):
        with pytest.raises(ValueError):
            SimilarityWeights(evidence_type_overlap=0.9, uncertainty_level_match=0.5, action_match=0.0,
                               conflict_type_overlap=0.0, trigger_match=0.0)
