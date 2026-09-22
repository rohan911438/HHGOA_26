"""Phase 2G - the deterministic case manager.

    InvestigationSnapshot + UncertaintyAssessment + PolicyDecision
            v
    CaseManager.create_case() / add_finding() / add_decision() /
    add_action_from_policy_decision() / set_outcome() /
    transition_status() / attach_evidence()
            v
    CaseRecord (via CaseStore)

`CaseManager` never imports `app.tigergraph` - it consumes only the
already-built `InvestigationSnapshot`/`UncertaintyAssessment`/
`PolicyDecision` objects a caller hands it (see
tests/unit/test_case_management.py::TestArchitecturalBoundary).

Every mutating method reads the current `CaseRecord` from its
`CaseStore`, builds a new record via `model_copy(update=...)` (never
mutates in place - matching every prior phase's frozen-model
convention), saves it back, and returns it. `id_generator` and `clock`
are injectable, exactly like `InvestigationService` (Phase 2D), so tests
never depend on `time.sleep()`, the real wall clock, or random IDs.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from app.case.models import (
    CaseAction,
    CaseActionStatus,
    CaseDecision,
    CaseFinding,
    CaseOutcome,
    CaseOutcomeType,
    CaseRecord,
    CaseStatus,
    CaseTrigger,
    DecisionType,
)
from app.case.store import CaseStore
from app.evidence.models import EvidenceBundle
from app.investigation.snapshot import InvestigationSnapshot
from app.policy.engine import material_evidence
from app.policy.models import ApprovalRoute, PolicyDecision
from app.uncertainty.models import UncertaintyAssessment

IdGenerator = Callable[[], str]
Clock = Callable[[], datetime]

# The enforced case state machine. CLOSED has no outgoing transitions -
# terminal. PENDING_REVIEW -> INVESTIGATING covers "reviewer sends the
# case back for more work" (a reasonable addition beyond the brief's
# literal list, documented here rather than silently added).
LEGAL_TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    CaseStatus.OPEN: frozenset({CaseStatus.INVESTIGATING}),
    CaseStatus.INVESTIGATING: frozenset({CaseStatus.PENDING_EVIDENCE, CaseStatus.ACTION_RECOMMENDED}),
    CaseStatus.PENDING_EVIDENCE: frozenset({CaseStatus.INVESTIGATING}),
    CaseStatus.ACTION_RECOMMENDED: frozenset({CaseStatus.PENDING_REVIEW}),
    CaseStatus.PENDING_REVIEW: frozenset({CaseStatus.CLOSED, CaseStatus.INVESTIGATING}),
    CaseStatus.CLOSED: frozenset(),
}


class CaseNotFoundError(KeyError):
    pass


class InvalidCaseTransitionError(ValueError):
    pass


class CaseActionNotFoundError(KeyError):
    pass


def _default_case_id() -> str:
    return f"case-{uuid.uuid4().hex}"


def _default_clock() -> datetime:
    return datetime.now(UTC)


def _material_evidence_types(bundle: EvidenceBundle | None) -> list[str]:
    """Which evidence types actually produced a material finding -
    reuses app.policy.engine.material_evidence (Phase 2F) rather than
    re-deriving the SUCCESS+related-count filter. Sorted for determinism.
    Used as a case-level similarity feature (manager.py/memory.py) - not
    every evidence type the investigation *attempted* (Phase 2D always
    attempts all six for a COMPLETED investigation, which would make
    that far less discriminating)."""
    return sorted({e.evidence_type.value for e in material_evidence(bundle)})


class CaseManager:
    def __init__(
        self,
        store: CaseStore,
        *,
        id_generator: IdGenerator = _default_case_id,
        clock: Clock = _default_clock,
    ) -> None:
        self._store = store
        self._id_generator = id_generator
        self._clock = clock

    # ------------------------------------------------------------ creation

    def create_case(
        self,
        snapshot: InvestigationSnapshot,
        *,
        uncertainty_assessment: UncertaintyAssessment | None = None,
        policy_decision: PolicyDecision | None = None,
        trigger: CaseTrigger = CaseTrigger.UNKNOWN,
    ) -> CaseRecord:
        now = self._clock()
        bundle = snapshot.evidence_bundle
        evidence_ids = sorted(e.evidence_id for e in bundle.evidence) if bundle is not None else []

        record = CaseRecord(
            case_id=self._id_generator(),
            status=CaseStatus.OPEN,
            trigger=trigger,
            transaction_id=snapshot.transaction_id,
            investigation_id=snapshot.investigation_id,
            created_at=now,
            updated_at=now,
            evidence_ids=evidence_ids,
            evidence_types=_material_evidence_types(bundle),
            findings=[],
            decisions=[],
            actions=[],
            outcome=None,
            uncertainty_assessment=uncertainty_assessment,
            policy_decision=policy_decision,
            notes=[],
        )
        self._store.save(record)
        return record

    # ------------------------------------------------------------ retrieval

    def get_case(self, case_id: str) -> CaseRecord:
        record = self._store.get(case_id)
        if record is None:
            raise CaseNotFoundError(case_id)
        return record

    # ------------------------------------------------------------ state machine

    def transition_status(self, case_id: str, new_status: CaseStatus) -> CaseRecord:
        record = self.get_case(case_id)
        legal = LEGAL_TRANSITIONS.get(record.status, frozenset())
        if new_status not in legal:
            raise InvalidCaseTransitionError(
                f"cannot transition case '{case_id}' from {record.status.value} to "
                f"{new_status.value}; legal transitions from {record.status.value}: "
                f"{sorted(s.value for s in legal) or '(none - terminal state)'}"
            )
        return self._save(record.model_copy(update={"status": new_status, "updated_at": self._clock()}))

    # ------------------------------------------------------------ findings

    def add_finding(
        self,
        case_id: str,
        *,
        description: str,
        evidence_ids: list[str],
        quality=None,
        finding_id: str | None = None,
    ) -> CaseRecord:
        record = self.get_case(case_id)
        finding = CaseFinding(
            finding_id=finding_id or self._id_generator(),
            description=description,
            evidence_ids=list(evidence_ids),
            quality=quality,
            created_at=self._clock(),
        )
        return self._save(
            record.model_copy(update={"findings": [*record.findings, finding], "updated_at": self._clock()})
        )

    # ------------------------------------------------------------ decisions

    def add_decision(
        self,
        case_id: str,
        *,
        decision_type: DecisionType,
        rationale: str,
        evidence_ids: list[str],
        actor: str,
        approval_route: ApprovalRoute | None = None,
        approved: bool | None = None,
        decision_id: str | None = None,
    ) -> CaseRecord:
        record = self.get_case(case_id)
        decision = CaseDecision(
            decision_id=decision_id or self._id_generator(),
            decision_type=decision_type,
            rationale=rationale,
            evidence_ids=list(evidence_ids),
            actor=actor,
            approval_route=approval_route,
            approved=approved,
            created_at=self._clock(),
        )
        return self._save(
            record.model_copy(update={"decisions": [*record.decisions, decision], "updated_at": self._clock()})
        )

    def add_system_recommendation(self, case_id: str, policy_decision: PolicyDecision) -> CaseRecord:
        """The only way a PolicyDecision becomes a CaseDecision - always
        decision_type=SYSTEM_RECOMMENDATION, never HUMAN_DECISION. The
        engine's recommendation is never presented as a human choice."""
        return self.add_decision(
            case_id,
            decision_type=DecisionType.SYSTEM_RECOMMENDATION,
            rationale=policy_decision.rationale,
            evidence_ids=list(policy_decision.evidence_ids),
            actor="policy_engine",
            approval_route=policy_decision.approval_route if policy_decision.approval_required else None,
        )

    # ------------------------------------------------------------ actions

    def add_action_from_policy_decision(
        self, case_id: str, policy_decision: PolicyDecision, *, actor: str = "policy_engine"
    ) -> CaseRecord:
        """Records `policy_decision.action` as a case action with
        status=RECOMMENDED, executed=False. A recommendation never
        becomes an executed action by itself - see
        `record_action_review`/`record_action_executed`."""
        record = self.get_case(case_id)
        action = CaseAction(
            action_id=self._id_generator(),
            action=policy_decision.action,
            status=CaseActionStatus.RECOMMENDED,
            rationale=policy_decision.rationale,
            approval_required=policy_decision.approval_required,
            approval_route=policy_decision.approval_route,
            executed=False,
            actor=actor,
            created_at=self._clock(),
        )
        return self._save(
            record.model_copy(update={"actions": [*record.actions, action], "updated_at": self._clock()})
        )

    def record_action_review(self, case_id: str, action_id: str, *, approved: bool, actor: str) -> CaseRecord:
        """Records a human reviewer's approve/reject decision on a
        previously-recommended action. Never sets executed=True."""
        record = self.get_case(case_id)
        action = self._find_action(record, action_id)
        new_status = CaseActionStatus.APPROVED if approved else CaseActionStatus.REJECTED
        updated_action = action.model_copy(update={"status": new_status})
        return self._save(
            record.model_copy(
                update={
                    "actions": self._replace_action(record, action_id, updated_action),
                    "updated_at": self._clock(),
                }
            )
        )

    def record_action_executed(self, case_id: str, action_id: str, *, actor: str) -> CaseRecord:
        """Records that an action was carried out BY SOME EXTERNAL SYSTEM
        (this project has no backend that actually performs any of these
        actions - Phase 2F). `CaseManager` never performs a real-world
        action; it only records that a caller asserted one happened.
        Only legal once the action has been APPROVED."""
        record = self.get_case(case_id)
        action = self._find_action(record, action_id)
        if action.status != CaseActionStatus.APPROVED:
            raise InvalidCaseTransitionError(
                f"action '{action_id}' must be APPROVED before it can be recorded as executed "
                f"(current status: {action.status.value})"
            )
        updated_action = action.model_copy(update={"status": CaseActionStatus.EXECUTED, "executed": True})
        return self._save(
            record.model_copy(
                update={
                    "actions": self._replace_action(record, action_id, updated_action),
                    "updated_at": self._clock(),
                }
            )
        )

    @staticmethod
    def _find_action(record: CaseRecord, action_id: str) -> CaseAction:
        for a in record.actions:
            if a.action_id == action_id:
                return a
        raise CaseActionNotFoundError(f"action '{action_id}' not found on case '{record.case_id}'")

    @staticmethod
    def _replace_action(record: CaseRecord, action_id: str, replacement: CaseAction) -> list[CaseAction]:
        return [replacement if a.action_id == action_id else a for a in record.actions]

    # ------------------------------------------------------------ outcome

    def set_outcome(
        self,
        case_id: str,
        outcome_type: CaseOutcomeType,
        *,
        notes: str = "",
        is_synthetic: bool = False,
    ) -> CaseRecord:
        record = self.get_case(case_id)
        outcome = CaseOutcome(
            outcome_type=outcome_type, notes=notes, recorded_at=self._clock(), is_synthetic=is_synthetic
        )
        return self._save(record.model_copy(update={"outcome": outcome, "updated_at": self._clock()}))

    # ------------------------------------------------------------ evidence

    def attach_evidence(self, case_id: str, evidence_ids: list[str]) -> CaseRecord:
        record = self.get_case(case_id)
        merged = sorted(set(record.evidence_ids) | set(evidence_ids))
        return self._save(record.model_copy(update={"evidence_ids": merged, "updated_at": self._clock()}))

    # ------------------------------------------------------------ internal

    def _save(self, record: CaseRecord) -> CaseRecord:
        self._store.save(record)
        return record
