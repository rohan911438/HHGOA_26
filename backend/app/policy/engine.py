"""Phase 2F - the deterministic policy / next-best-action engine.

    InvestigationSnapshot (Phase 2D) + UncertaintyAssessment (Phase 2E)
            v
    PolicyEngine.evaluate()
            v
    PolicyDecision

This module never imports `app.tigergraph` and never calls TigerGraph -
verified the same way as app.uncertainty (tests/unit/test_policy_engine.py
::TestArchitecturalBoundary parses this file's own imports with `ast`).
No LLM, no machine learning, no probabilistic fraud scoring, no hidden
score: `evaluate()` is a pure, deterministic function of its two typed
inputs plus this engine's own fixed, documented configuration.

**This module never reads `EvidenceBundle.dataset_risk_score`** - the
dataset's binary isFraud label never influences the decision. Verified
behaviorally (identical decision with the label flipped) and statically
(AST check for any `dataset_risk_score`/`isFraud`/`is_fraud` access) in
the test module.

## Decision logic (see docs/phase-2-policy-nba.md for the full writeup)

Gate 1 - cannot act at all:
    `snapshot.status == FAILED` or `uncertainty_level == UNKNOWN`
    -> REQUEST_MORE_EVIDENCE, status=NOT_ACTIONABLE, no approval needed
       (there is nothing yet to approve or deny).

Gate 2 - investigation ran, but Phase 2E judged it insufficient
(`not assessment.sufficient_for_next_stage`):
    - a HIGH-severity conflict is present (SIGNAL_DISAGREEMENT or
      DATA_INCONSISTENCY - the underlying facts actually disagree, which
      collecting more of the *same* evidence would not fix)
      -> ESCALATE_ANALYST, status=HUMAN_REVIEW_REQUIRED
    - otherwise (insufficient because of low coverage)
      -> REQUEST_MORE_EVIDENCE, status=MORE_EVIDENCE_REQUIRED

Gate 3 - sufficient for the next stage (Gate 2 guarantees no HIGH-severity
conflict remains here - Phase 2E's own sufficiency rule already excludes
that case):
    "Material evidence" = SUCCESS-status shared_card/device/address/
    email_domain items with at least one related transaction - a query
    that ran and found nothing (EMPTY) is not material, by design
    (Phase 2E/2B's EMPTY-is-not-a-signal distinction, preserved here).

    - no material evidence at all
      -> ALLOW_TRANSACTION, no approval needed (Phase 2F §6: a completed
         investigation with weak/no meaningful signal must not receive a
         punitive action regardless of the numeric uncertainty level)
    - uncertainty_level == HIGH (and material evidence exists)
      -> ESCALATE_ANALYST (never an aggressive/irreversible action under
         high uncertainty, per Phase 2F §5)
    - uncertainty_level == MEDIUM
      -> CREATE_CASE if at least one material item is MEDIUM/HIGH quality
         and no conflict was detected; otherwise MONITOR_ACCOUNT
    - uncertainty_level == LOW
      -> CREATE_CASE if any (even low-severity) conflict remains -
         conflicts cap how aggressive a LOW-uncertainty recommendation
         may be
      -> otherwise, among only-LOW-quality material evidence:
         WARN_CUSTOMER if >=2 distinct evidence types corroborate,
         else MONITOR_ACCOUNT (a single LOW-quality signal, e.g. shared
         Address alone, must never drive escalation - Phase 2F §6/§h)
      -> otherwise (at least one MEDIUM/HIGH-quality material item):
         BLOCK_TRANSACTION if >=2 distinct evidence types corroborate
         (configurable via `min_distinct_types_for_block`), else
         CREATE_CASE (a single strong signal alone is a case, not a
         block)

Every branch that recommends anything other than ALLOW_TRANSACTION or
REQUEST_MORE_EVIDENCE sets `approval_required=True` - this project never
lets the engine's own recommendation double as authorization.
`executable` is always `False` (module docstring / models.py).
"""

from __future__ import annotations

from app.evidence.models import Evidence, EvidenceBundle, EvidenceType, QueryStatus, SignalQuality
from app.investigation.snapshot import InvestigationSnapshot, InvestigationStatus
from app.policy.models import (
    POLICY_STATUS_DISCLAIMER,
    ApprovalRoute,
    PolicyAction,
    PolicyDecision,
    PolicyStatus,
)
from app.uncertainty.models import ConflictSeverity, UncertaintyAssessment, UncertaintyLevel

_MATERIAL_TYPES = (
    EvidenceType.SHARED_CARD,
    EvidenceType.SHARED_DEVICE,
    EvidenceType.SHARED_ADDRESS,
    EvidenceType.SHARED_EMAIL_DOMAIN,
)

# Actions that never require sign-off in this development heuristic:
# "do nothing differently" (ALLOW) and "go collect more data"
# (REQUEST_MORE_EVIDENCE) have no real-world consequence to authorize.
_NO_APPROVAL_ACTIONS = frozenset({PolicyAction.ALLOW_TRANSACTION, PolicyAction.REQUEST_MORE_EVIDENCE})


def material_evidence(bundle: EvidenceBundle | None) -> list[Evidence]:
    """SUCCESS-status shared-entity/email evidence with at least one
    related transaction. EMPTY (ran fine, found nothing) is never
    material - preserving the Phase 2B/2E distinction exactly.

    Public so app.case (Phase 2G) can derive the same "which evidence
    types actually produced a finding" concept for case-similarity
    features without re-deriving this filter - see
    app/case/manager.py."""
    if bundle is None:
        return []
    result = []
    for e in bundle.evidence:
        if e.evidence_type not in _MATERIAL_TYPES or e.status != QueryStatus.SUCCESS:
            continue
        count = e.metrics.get("related_transaction_count")
        if count is None:
            count = e.metrics.get("purchaser_related_count", 0) + e.metrics.get("recipient_related_count", 0)
        if count and count > 0:
            result.append(e)
    return result


class PolicyEngine:
    def __init__(self, *, min_distinct_types_for_block: int = 2) -> None:
        if min_distinct_types_for_block < 1:
            raise ValueError("min_distinct_types_for_block must be at least 1")
        self._min_distinct_types_for_block = min_distinct_types_for_block

    def evaluate(
        self, snapshot: InvestigationSnapshot, assessment: UncertaintyAssessment
    ) -> PolicyDecision:
        bundle = snapshot.evidence_bundle
        evidence_ids = sorted(e.evidence_id for e in bundle.evidence) if bundle is not None else []

        if snapshot.status == InvestigationStatus.FAILED or assessment.uncertainty_level == UncertaintyLevel.UNKNOWN:
            return self._decision(
                snapshot, assessment, evidence_ids,
                action=PolicyAction.REQUEST_MORE_EVIDENCE,
                status=PolicyStatus.NOT_ACTIONABLE,
                approval_route=ApprovalRoute.NONE,
                basis_detail="Gate 1: investigation did not establish a usable transaction context.",
                rationale_detail=(
                    "The investigation could not establish a usable transaction context "
                    f"(snapshot status={snapshot.status.value}), so no evidence-based "
                    "recommendation can be made yet."
                ),
            )

        if not assessment.sufficient_for_next_stage:
            high_conflict = any(c.severity == ConflictSeverity.HIGH for c in assessment.conflicting_evidence)
            if high_conflict:
                return self._decision(
                    snapshot, assessment, evidence_ids,
                    action=PolicyAction.ESCALATE_ANALYST,
                    status=PolicyStatus.HUMAN_REVIEW_REQUIRED,
                    approval_route=ApprovalRoute.SENIOR_ANALYST,
                    basis_detail="Gate 2a: a HIGH-severity evidence conflict was detected.",
                    rationale_detail=(
                        "A HIGH-severity evidence conflict was detected "
                        f"({', '.join(c.conflict_type.value for c in assessment.conflicting_evidence if c.severity == ConflictSeverity.HIGH)}), "
                        "meaning two facts that should agree do not. Collecting more of the "
                        "same evidence would not resolve this - human review is recommended "
                        "instead of an automated recommendation."
                    ),
                )
            return self._decision(
                snapshot, assessment, evidence_ids,
                action=PolicyAction.REQUEST_MORE_EVIDENCE,
                status=PolicyStatus.MORE_EVIDENCE_REQUIRED,
                approval_route=ApprovalRoute.NONE,
                basis_detail="Gate 2b: evidence coverage was insufficient for the next stage.",
                rationale_detail=(
                    f"Evidence coverage is {assessment.evidence_coverage:.0%}, below the "
                    "threshold this project's uncertainty engine requires before treating an "
                    "investigation as sufficient. Additional evidence is recommended before any "
                    "action is considered."
                ),
            )

        material = material_evidence(bundle)
        has_conflict = bool(assessment.conflicting_evidence)  # only LOW/MEDIUM severity can reach here

        if not material:
            return self._decision(
                snapshot, assessment, evidence_ids,
                action=PolicyAction.ALLOW_TRANSACTION,
                status=PolicyStatus.RECOMMENDATION_READY,
                approval_route=ApprovalRoute.NONE,
                basis_detail="Gate 3a: sufficient investigation, no material shared-entity evidence found.",
                rationale_detail=(
                    "The investigation is complete and no shared-card/device/address/email "
                    "evidence with any related transaction was found. A completed investigation "
                    "with no meaningful signal does not receive a punitive action under this "
                    "project's development heuristic."
                ),
            )

        if assessment.uncertainty_level == UncertaintyLevel.HIGH:
            return self._decision(
                snapshot, assessment, evidence_ids,
                action=PolicyAction.ESCALATE_ANALYST,
                status=PolicyStatus.HUMAN_REVIEW_REQUIRED,
                approval_route=ApprovalRoute.ANALYST,
                basis_detail="Gate 3b: material evidence present under HIGH uncertainty.",
                rationale_detail=(
                    "Material evidence was found, but overall investigation uncertainty is HIGH. "
                    "This project's heuristic never recommends an aggressive or irreversible "
                    "action under high uncertainty - escalating to an analyst instead."
                ),
            )

        has_strong = self._has_medium_or_high_quality(material)
        distinct_types = self._distinct_material_types(material)

        if assessment.uncertainty_level == UncertaintyLevel.MEDIUM:
            if has_strong and not has_conflict:
                action = PolicyAction.CREATE_CASE
                detail = "at least one MEDIUM/HIGH-quality material signal, no conflict"
            else:
                action = PolicyAction.MONITOR_ACCOUNT
                detail = "material evidence present, but only LOW-quality and/or a conflict was noted"
            return self._decision(
                snapshot, assessment, evidence_ids,
                action=action,
                status=PolicyStatus.RECOMMENDATION_READY,
                approval_route=ApprovalRoute.ANALYST,
                basis_detail=f"Gate 3c: MEDIUM uncertainty, {detail}.",
                rationale_detail=(
                    f"Material evidence was found under MEDIUM uncertainty ({detail}). "
                    f"Recommending {action.value}, routed to an analyst for approval."
                ),
            )

        # uncertainty_level == LOW
        if has_conflict:
            return self._decision(
                snapshot, assessment, evidence_ids,
                action=PolicyAction.CREATE_CASE,
                status=PolicyStatus.RECOMMENDATION_READY,
                approval_route=ApprovalRoute.ANALYST,
                basis_detail="Gate 3d: LOW uncertainty, but a conflict caps the recommendation.",
                rationale_detail=(
                    "Uncertainty is LOW and material evidence was found, but at least one "
                    "evidence conflict remains - this caps the recommendation at CREATE_CASE "
                    "rather than escalating to BLOCK_TRANSACTION."
                ),
            )

        if not has_strong:
            action = PolicyAction.WARN_CUSTOMER if distinct_types >= 2 else PolicyAction.MONITOR_ACCOUNT
            return self._decision(
                snapshot, assessment, evidence_ids,
                action=action,
                status=PolicyStatus.RECOMMENDATION_READY,
                approval_route=ApprovalRoute.ANALYST,
                basis_detail=(
                    f"Gate 3e: LOW uncertainty, only LOW-quality material evidence "
                    f"across {distinct_types} distinct type(s)."
                ),
                rationale_detail=(
                    f"Only LOW-quality material evidence was found, across {distinct_types} "
                    "distinct evidence type(s). LOW-quality signals (e.g. coarse shared-address "
                    "codes) are never treated as equivalent to stronger evidence, so the "
                    f"recommendation stays conservative: {action.value}."
                ),
            )

        if distinct_types >= self._min_distinct_types_for_block:
            return self._decision(
                snapshot, assessment, evidence_ids,
                action=PolicyAction.BLOCK_TRANSACTION,
                status=PolicyStatus.RECOMMENDATION_READY,
                approval_route=ApprovalRoute.SENIOR_ANALYST,
                basis_detail=(
                    f"Gate 3f: LOW uncertainty, MEDIUM/HIGH-quality evidence corroborated "
                    f"across {distinct_types} distinct type(s) (>= {self._min_distinct_types_for_block})."
                ),
                rationale_detail=(
                    f"Uncertainty is LOW and MEDIUM/HIGH-quality material evidence is "
                    f"corroborated across {distinct_types} distinct evidence types, with no "
                    "conflicts. This is the strongest, most corroborated picture this heuristic "
                    "recognizes - recommending BLOCK_TRANSACTION, routed to a senior analyst for "
                    "approval before any real-world action could be taken."
                ),
            )

        return self._decision(
            snapshot, assessment, evidence_ids,
            action=PolicyAction.CREATE_CASE,
            status=PolicyStatus.RECOMMENDATION_READY,
            approval_route=ApprovalRoute.ANALYST,
            basis_detail=(
                f"Gate 3g: LOW uncertainty, a single MEDIUM/HIGH-quality material signal "
                f"(< {self._min_distinct_types_for_block} distinct types)."
            ),
            rationale_detail=(
                "Uncertainty is LOW and a MEDIUM/HIGH-quality material signal was found, but "
                f"only from a single evidence type (fewer than {self._min_distinct_types_for_block} "
                "distinct types corroborate it). A single strong signal alone is a case, not "
                "yet a block."
            ),
        )

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _has_medium_or_high_quality(material: list[Evidence]) -> bool:
        return any(e.quality in (SignalQuality.MEDIUM, SignalQuality.HIGH) for e in material)

    @staticmethod
    def _distinct_material_types(material: list[Evidence]) -> int:
        return len({e.evidence_type for e in material})

    def _decision(
        self,
        snapshot: InvestigationSnapshot,
        assessment: UncertaintyAssessment,
        evidence_ids: list[str],
        *,
        action: PolicyAction,
        status: PolicyStatus,
        approval_route: ApprovalRoute,
        basis_detail: str,
        rationale_detail: str,
    ) -> PolicyDecision:
        approval_required = action not in _NO_APPROVAL_ACTIONS
        return PolicyDecision(
            investigation_id=snapshot.investigation_id,
            transaction_id=snapshot.transaction_id,
            action=action,
            approval_required=approval_required,
            approval_route=approval_route if approval_required else ApprovalRoute.NONE,
            executable=False,
            requires_more_evidence=(action == PolicyAction.REQUEST_MORE_EVIDENCE),
            status=status,
            uncertainty_level=assessment.uncertainty_level,
            overall_uncertainty=assessment.overall_uncertainty,
            evidence_ids=evidence_ids,
            policy_basis=f"{POLICY_STATUS_DISCLAIMER}. {basis_detail}",
            rationale=(
                f"{rationale_detail} "
                f"Approval required: {approval_required}"
                f"{f' (route: {approval_route.value})' if approval_required else ''}. "
                "This is a recommendation only - no real-world action has been executed."
            ),
        )
