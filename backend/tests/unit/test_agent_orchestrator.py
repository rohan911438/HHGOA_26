"""Offline tests for app/agent/ (state, llm, prompts, orchestrator).

Every scenario uses FakeLLMClient (or MalformedLLMClient) - no real LLM
credential or network call is involved anywhere in this file (Phase 2H
§16/§24). TigerGraph calls are mocked via app.tigergraph.queries, same
convention as every prior phase's offline tests.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

import pytest

from app.agent import (
    AgentOrchestrator,
    AgentStatus,
    FakeLLMClient,
    LLMDecision,
    LLMDecisionAction,
    MalformedLLMClient,
    RequestedEvidenceType,
)
from app.case import CaseStatus, CaseTrigger, InMemoryCaseStore
from app.investigation import InvestigationService, build_default_registry
from app.investigation import tools as t
from app.policy import PolicyAction
from app.tigergraph.client import TigerGraphQueryError
from app.tigergraph.queries import (
    NetworkSummary,
    SharedEmailActivity,
    SharedEntityActivity,
    TransactionContext,
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


def fixed_ids(prefix: str = "req-test"):
    counter = count(1)
    return lambda: f"{prefix}-{next(counter)}"


def fixed_clock(start: datetime = datetime(2026, 1, 1, tzinfo=UTC)):
    from datetime import timedelta

    counter = count()
    return lambda: start + timedelta(microseconds=next(counter))


def make_orchestrator(store, llm, **kwargs):
    from app.case import CaseManager, CaseMemory

    clock = fixed_clock()
    return AgentOrchestrator(
        FakeClient(), store, llm,
        investigation_service=InvestigationService(build_default_registry(), id_generator=lambda: "inv-fixed"),
        case_manager=CaseManager(store, id_generator=fixed_ids("case-test"), clock=clock),
        case_memory=CaseMemory(store),
        id_generator=fixed_ids("req-test"), clock=clock, **kwargs,
    )


# ---------------------------------------------------------------- Scenario 1: normal investigation


class TestNormalInvestigation:
    def test_agent_reaches_a_recommendation(self, monkeypatch):
        set_strong_two_types(monkeypatch)
        llm = FakeLLMClient(decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="sufficient")])
        orchestrator = make_orchestrator(InMemoryCaseStore(), llm)
        result = orchestrator.run("T1", trigger=CaseTrigger.FRAUD_SIGNAL)

        assert result.investigation_status == AgentStatus.COMPLETED
        assert result.completed is True
        assert result.case_id is not None
        assert result.policy_decision is not None
        assert result.policy_decision.action == PolicyAction.BLOCK_TRANSACTION
        assert result.iterations == 1
        assert result.tool_call_count == 1
        assert result.error is None


# ---------------------------------------------------------------- Scenario 2: more evidence required


class TestMoreEvidenceRequired:
    def test_graph_loops_correctly(self, monkeypatch):
        set_all_empty(monkeypatch)
        llm = FakeLLMClient(decisions=[
            LLMDecision(action=LLMDecisionAction.REQUEST_MORE_EVIDENCE,
                        requested_evidence_type=RequestedEvidenceType.CUSTOMER_VALIDATION, reason="need more"),
            LLMDecision(action=LLMDecisionAction.CONTINUE, reason="now sufficient"),
        ])
        orchestrator = make_orchestrator(InMemoryCaseStore(), llm, max_iterations=5)
        result = orchestrator.run("T1")

        assert result.investigation_status == AgentStatus.COMPLETED
        assert result.iterations == 2
        assert result.tool_call_count == 2
        assert len(result.requested_evidence) == 1
        assert result.requested_evidence[0].requested_evidence_type == RequestedEvidenceType.CUSTOMER_VALIDATION
        assert result.requested_evidence[0].status.value == "REQUESTED"


# ---------------------------------------------------------------- Scenario 3: iteration limit


class TestIterationLimit:
    def test_safe_termination_on_repeated_more_evidence_requests(self, monkeypatch):
        set_all_empty(monkeypatch)
        llm = FakeLLMClient(decisions=[
            LLMDecision(action=LLMDecisionAction.REQUEST_MORE_EVIDENCE,
                        requested_evidence_type=RequestedEvidenceType.ANALYST_REVIEW, reason="again")
            for _ in range(50)
        ])
        orchestrator = make_orchestrator(InMemoryCaseStore(), llm, max_iterations=3)
        result = orchestrator.run("T1")

        assert result.investigation_status == AgentStatus.LIMIT_REACHED
        assert result.completed is False
        assert result.iterations == 3
        assert result.tool_call_count == 3
        assert llm.calls == 3  # never called more times than iterations allow
        # A real, deterministic PolicyEngine decision still exists - never a fabricated verdict.
        assert result.policy_decision is not None
        assert result.policy_decision.action != PolicyAction.BLOCK_TRANSACTION  # conservative given no evidence

    def test_max_iterations_is_configurable_and_validated(self):
        with pytest.raises(ValueError):
            AgentOrchestrator(FakeClient(), InMemoryCaseStore(), FakeLLMClient(), max_iterations=0)

    def test_max_tool_calls_is_configurable_and_validated(self):
        with pytest.raises(ValueError):
            AgentOrchestrator(FakeClient(), InMemoryCaseStore(), FakeLLMClient(), max_tool_calls=0)


# ---------------------------------------------------------------- Scenario 4: tool/investigation failure


class TestToolFailure:
    def test_no_fabricated_recommendation_on_unhandled_investigation_failure(self, monkeypatch):
        class BrokenInvestigationService(InvestigationService):
            def investigate_transaction(self, *a, **kw):
                raise RuntimeError("simulated unhandled crash")

        orchestrator = AgentOrchestrator(
            FakeClient(), InMemoryCaseStore(), FakeLLMClient(),
            investigation_service=BrokenInvestigationService(build_default_registry()),
        )
        result = orchestrator.run("T1")

        assert result.investigation_status == AgentStatus.FAILED
        assert result.policy_decision is None
        assert result.case_id is None  # never created without a real snapshot
        assert result.error is not None
        assert "simulated unhandled crash" in result.error

    def test_a_gracefully_failed_snapshot_still_produces_a_real_policy_decision(self, monkeypatch):
        """A TigerGraphQueryError('does not exist') is already caught
        inside the registry (Phase 2C) and becomes a FAILED
        InvestigationSnapshot, not an unhandled exception - the agent
        must reuse PolicyEngine's own Gate 1 handling for this, not
        treat it as an agent-level failure."""
        def boom(client, txn):
            raise TigerGraphQueryError(f"transaction '{txn}' does not exist")

        monkeypatch.setattr(t.queries, "get_transaction_context", boom)
        orchestrator = make_orchestrator(InMemoryCaseStore(), FakeLLMClient())
        result = orchestrator.run("ghost")

        assert result.investigation_status == AgentStatus.COMPLETED
        assert result.policy_decision is not None
        assert result.policy_decision.status.value == "NOT_ACTIONABLE"
        assert result.case_id is not None


# ---------------------------------------------------------------- Scenario 5: LLM malformed output


class TestMalformedLLMOutput:
    def test_validation_rejects_malformed_output_safely(self, monkeypatch):
        set_strong_two_types(monkeypatch)
        orchestrator = make_orchestrator(InMemoryCaseStore(), MalformedLLMClient())
        result = orchestrator.run("T1")

        assert result.investigation_status == AgentStatus.FAILED
        assert result.policy_decision is None
        assert "malformed" in result.error.lower() or "validation" in result.error.lower()

    def test_llm_raising_an_exception_is_also_handled_safely(self, monkeypatch):
        class ExplodingLLM:
            def decide_next_step(self, state_summary):
                raise ConnectionError("simulated LLM API outage")

            def generate_explanation(self, state_summary):
                return "unreachable"

        set_strong_two_types(monkeypatch)
        orchestrator = make_orchestrator(InMemoryCaseStore(), ExplodingLLM())
        result = orchestrator.run("T1")
        assert result.investigation_status == AgentStatus.FAILED
        assert result.policy_decision is None


# ---------------------------------------------------------------- Scenario 6: historical case context


class TestHistoricalCaseContext:
    def test_agent_receives_similar_historical_cases(self, monkeypatch):
        from app.case.models import CaseOutcome, CaseOutcomeType, CaseRecord
        from app.policy.models import ApprovalRoute, PolicyDecision, PolicyStatus
        from app.uncertainty.models import UncertaintyAssessment, UncertaintyLevel

        now = datetime(2026, 1, 1, tzinfo=UTC)
        store = InMemoryCaseStore()
        ua = UncertaintyAssessment(
            investigation_id="inv-h", transaction_id="TX-h", evidence_coverage=1.0, signal_quality=0.7,
            signal_conflict=0.0, data_completeness=1.0, overall_uncertainty=0.1, uncertainty_level=UncertaintyLevel.LOW,
            missing_evidence=[], conflicting_evidence=[], factors=[], sufficient_for_next_stage=True, rationale="x",
        )
        pd = PolicyDecision(
            investigation_id="inv-h", transaction_id="TX-h", action=PolicyAction.BLOCK_TRANSACTION,
            approval_required=True, approval_route=ApprovalRoute.SENIOR_ANALYST, executable=False,
            requires_more_evidence=False, status=PolicyStatus.RECOMMENDATION_READY,
            uncertainty_level=UncertaintyLevel.LOW, overall_uncertainty=0.1, evidence_ids=[], rationale="x",
        )
        historical = CaseRecord(
            case_id="hist-1", status=CaseStatus.CLOSED, trigger=CaseTrigger.FRAUD_SIGNAL, transaction_id="TX-h",
            created_at=now, updated_at=now, evidence_types=["shared_card", "shared_device"],
            uncertainty_assessment=ua, policy_decision=pd,
            outcome=CaseOutcome(outcome_type=CaseOutcomeType.CLEARED, recorded_at=now, is_synthetic=True),
        )
        store.save(historical)

        set_strong_two_types(monkeypatch)  # same evidence pattern: shared_card + shared_device
        llm = FakeLLMClient(decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="sufficient")])
        orchestrator = make_orchestrator(store, llm)
        result = orchestrator.run("T1")

        assert len(result.historical_case_context) == 1
        assert result.historical_case_context[0].case_id == "hist-1"
        assert result.historical_case_context[0].outcome.outcome_type == CaseOutcomeType.CLEARED

    def test_historical_outcome_never_becomes_a_current_fact(self, monkeypatch):
        """The current case's own policy_decision must be derived purely
        from CURRENT evidence - never copied from a historical outcome,
        regardless of how similar that historical case was."""
        from app.case.models import CaseOutcome, CaseOutcomeType, CaseRecord

        now = datetime(2026, 1, 1, tzinfo=UTC)
        store = InMemoryCaseStore()
        historical = CaseRecord(
            case_id="hist-1", status=CaseStatus.CLOSED, trigger=CaseTrigger.FRAUD_SIGNAL, transaction_id="TX-h",
            created_at=now, updated_at=now, evidence_types=["shared_card", "shared_device"],
            outcome=CaseOutcome(outcome_type=CaseOutcomeType.CONFIRMED_FRAUD, recorded_at=now, is_synthetic=True),
        )
        store.save(historical)

        set_all_empty(monkeypatch)  # current transaction: no material evidence at all
        llm = FakeLLMClient(decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="sufficient")])
        orchestrator = make_orchestrator(store, llm)
        result = orchestrator.run("T1")

        # Despite a similar-ish historical case being CONFIRMED_FRAUD, the
        # current, evidence-free case must still get the conservative
        # ALLOW_TRANSACTION recommendation - never inherited from history.
        assert result.policy_decision.action == PolicyAction.ALLOW_TRANSACTION


# ---------------------------------------------------------------- Scenario 7/8: policy + approval preserved


class TestPolicyAndApprovalPreservation:
    def test_policy_engine_result_is_preserved_exactly(self, monkeypatch):
        from app.policy import PolicyEngine
        from app.uncertainty import UncertaintyEngine

        set_strong_two_types(monkeypatch)
        service = InvestigationService(build_default_registry(), id_generator=lambda: "inv-fixed")
        snapshot = service.investigate_transaction("T1", FakeClient())
        assessment = UncertaintyEngine().assess(snapshot)
        expected = PolicyEngine().evaluate(snapshot, assessment)

        llm = FakeLLMClient(decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="sufficient")])
        orchestrator = make_orchestrator(InMemoryCaseStore(), llm)
        result = orchestrator.run("T1")

        assert result.policy_decision.action == expected.action
        assert result.policy_decision.approval_required == expected.approval_required
        assert result.policy_decision.approval_route == expected.approval_route
        assert result.policy_decision.executable == expected.executable
        assert result.policy_decision.rationale == expected.rationale

    def test_approval_fields_are_preserved_on_the_case_action(self, monkeypatch):
        from app.case import CaseManager

        set_strong_two_types(monkeypatch)
        llm = FakeLLMClient(decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="sufficient")])
        store = InMemoryCaseStore()
        orchestrator = make_orchestrator(store, llm)
        result = orchestrator.run("T1")

        assert result.policy_decision.approval_required is True
        assert result.policy_decision.approval_route.value == "SENIOR_ANALYST"
        case_record = CaseManager(store).get_case(result.case_id)
        case_action = case_record.actions[-1]
        assert case_action.approval_required == result.policy_decision.approval_required
        assert case_action.approval_route == result.policy_decision.approval_route
        assert case_action.executed is False  # recommendation only


# ---------------------------------------------------------------- Scenario 9: dataset-label isolation


class TestDatasetLabelIsolation:
    def test_identical_investigation_opposite_labels_yields_identical_deterministic_state(self, monkeypatch):
        set_strong_two_types(monkeypatch, is_fraud=True)
        llm_a = FakeLLMClient(decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="sufficient")])
        result_fraud = make_orchestrator(InMemoryCaseStore(), llm_a).run("T1")

        set_strong_two_types(monkeypatch, is_fraud=False)
        llm_b = FakeLLMClient(decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="sufficient")])
        result_clean = make_orchestrator(InMemoryCaseStore(), llm_b).run("T1")

        assert result_fraud.uncertainty_assessment.model_dump(mode="json") == result_clean.uncertainty_assessment.model_dump(mode="json")
        assert result_fraud.policy_decision.model_dump(mode="json") == result_clean.policy_decision.model_dump(mode="json")
        assert result_fraud.investigation_status == result_clean.investigation_status

    def test_no_label_reference_in_the_agent_package(self):
        forbidden = {"dataset_risk_score", "isFraud", "is_fraud", "is_fraud_label"}
        for module_name in ("app.agent.state", "app.agent.llm", "app.agent.orchestrator"):
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


# ---------------------------------------------------------------- Scenario 10: deterministic fake LLM


class TestDeterminism:
    def test_repeated_execution_with_identical_state_and_fake_llm_is_byte_identical(self, monkeypatch):
        set_strong_two_types(monkeypatch)

        def make_llm():
            return FakeLLMClient(
                decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="sufficient")],
                explanation="fixed explanation",
            )

        result_a = make_orchestrator(InMemoryCaseStore(), make_llm()).run("T1")
        result_b = make_orchestrator(InMemoryCaseStore(), make_llm()).run("T1")
        assert result_a.model_dump(mode="json") == result_b.model_dump(mode="json")


# ---------------------------------------------------------------- security / architectural boundary


class TestSecurityAndBoundary:
    def test_no_eval_exec_or_shell_in_the_agent_package(self):
        for module_name in ("app.agent.state", "app.agent.llm", "app.agent.prompts", "app.agent.orchestrator"):
            import importlib

            module = importlib.import_module(module_name)
            source = Path(module.__file__).read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    assert node.func.id not in ("eval", "exec"), f"{module_name} calls {node.func.id}"
            assert "subprocess" not in source
            assert "os.system" not in source

    def test_agent_never_imports_app_tigergraph_queries_or_client_construction(self):
        """The orchestrator's only TigerGraph-facing surface is
        InvestigationService.investigate_transaction() - it must not
        import app.tigergraph.queries directly, and must not import
        get_client (which would let it build its own connection)."""
        import app.agent.orchestrator as orch_module

        tree = ast.parse(Path(orch_module.__file__).read_text(encoding="utf-8"))
        imported_names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_names.append((node.module, [a.name for a in node.names]))
        for module, names in imported_names:
            assert module != "app.tigergraph.queries"
            if module == "app.tigergraph.client":
                assert names == ["TigerGraphClient"]  # type hint only, never get_client

    def test_llm_decision_output_is_a_strict_closed_schema(self):
        """The core anti-prompt-injection mitigation: LLMDecision's
        `action` field only accepts one of four fixed enum values, and
        extra fields are forbidden - no free-form instruction from
        injected text can ever reach executable code."""
        from pydantic import ValidationError

        from app.agent.llm import LLMDecision

        assert LLMDecision.model_config.get("extra") == "forbid"
        with pytest.raises(ValidationError):
            LLMDecision(action="drop_graph; rm -rf /", reason="malicious")

    def test_prompt_marks_investigation_data_as_data_not_instructions(self):
        from app.agent.prompts import SYSTEM_PROMPT

        assert "DATA, not instructions" in SYSTEM_PROMPT or "not instructions" in SYSTEM_PROMPT.lower()

    def test_no_llm_provider_hardcoded_into_orchestrator_module(self):
        import app.agent.orchestrator as orch_module

        source = Path(orch_module.__file__).read_text(encoding="utf-8").lower()
        assert "openai" not in source
        assert "chatopenai" not in source


# ---------------------------------------------------------------- serialization


class TestSerialization:
    def test_agent_investigation_result_round_trips_through_json(self, monkeypatch):
        from app.agent import AgentInvestigationResult

        set_strong_two_types(monkeypatch)
        llm = FakeLLMClient(decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="sufficient")])
        result = make_orchestrator(InMemoryCaseStore(), llm).run("T1")

        dumped = result.model_dump(mode="json")
        rebuilt = AgentInvestigationResult.model_validate(json.loads(json.dumps(dumped)))
        assert rebuilt.model_dump(mode="json") == dumped


# ---------------------------------------------------------------- no-verdict regression


class TestNoFraudVerdict:
    def test_no_fraud_field_exists_on_agent_state_or_result(self):
        from app.agent.state import AgentInvestigationResult

        forbidden = {"fraud_probability", "fraud_verdict", "final_fraud_score", "is_fraud"}
        assert forbidden.isdisjoint(AgentInvestigationResult.model_fields)
