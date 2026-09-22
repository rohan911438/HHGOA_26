"""Phase 2I - the deterministic context builder.

    Evidence (Phase 2B) + UncertaintyAssessment (Phase 2E)
    + SimilarCaseResult / RecurringPattern (Phase 2G) + PolicyDecision (Phase 2F, optional)
            v
    ContextBuilder.build()
            v
    InvestigationContext

This module never imports `app.tigergraph` and never calls TigerGraph -
verified the same way as every deterministic component since Phase 2E
(tests/unit/test_context_builder.py::TestArchitecturalBoundary parses
this file's own imports with `ast`). It performs no TigerGraph queries
beyond whatever the caller already ran through `InvestigationService`
(Phase 2D) - `build()` and `build_for_case()` take already-computed
results as arguments; `build_for_case()`'s only "retrieval" call is to
`CaseMemory` (Phase 2G, unmodified), never a second graph traversal.

`ContextBuilder` never reads `EvidenceBundle.dataset_risk_score` or any
equivalent dataset label - `CURRENT_FACT` items are built only from
`Evidence.observation`/`.quality`/`.evidence_id`, and `HISTORICAL_CASE`
items only from `SimilarCaseResult`'s own fields (which itself has no
label field to leak, by Phase 2G's own construction). See
tests/unit/test_context_builder.py::TestLabelIsolation for the
behavioral + static proof.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.case.memory import CaseMemory
from app.case.models import CaseRecord, RecurringPattern, SimilarCaseResult
from app.context.models import ContextItem, ContextItemType, InvestigationContext
from app.evidence.models import QueryStatus
from app.investigation.snapshot import InvestigationSnapshot
from app.policy.models import PolicyDecision
from app.uncertainty.models import UncertaintyAssessment

Clock = Callable[[], datetime]
IdGenerator = Callable[[], str]


def _default_clock() -> datetime:
    return datetime.now(UTC)


def _default_context_id() -> str:
    return f"ctx-{uuid.uuid4().hex}"


@dataclass(frozen=True)
class ContextLimits:
    """Configurable, never hardcoded (Phase 2I §11/§12). Every default
    is deliberately conservative - a small, bounded amount of context,
    not an unbounded dump."""

    top_k_historical_cases: int = 5
    top_k_recurring_patterns: int = 5
    max_context_items: int = 30

    def __post_init__(self) -> None:
        if self.top_k_historical_cases < 0 or self.top_k_recurring_patterns < 0 or self.max_context_items < 1:
            raise ValueError("ContextLimits values must be non-negative (max_context_items >= 1)")


class ContextBuilder:
    def __init__(
        self,
        case_memory: CaseMemory | None = None,
        *,
        limits: ContextLimits | None = None,
        id_generator: IdGenerator = _default_context_id,
        clock: Clock = _default_clock,
    ) -> None:
        self._case_memory = case_memory
        self._limits = limits or ContextLimits()
        self._id_generator = id_generator
        self._clock = clock

    # ------------------------------------------------------------ convenience entry point

    def build_for_case(
        self,
        case_record: CaseRecord,
        snapshot: InvestigationSnapshot,
        assessment: UncertaintyAssessment,
        policy_decision: PolicyDecision | None = None,
    ) -> InvestigationContext:
        """Retrieves historical cases and recurring patterns via
        `CaseMemory` (Phase 2G, unmodified) and builds the full context.
        Requires a `CaseMemory` to have been provided at construction."""
        if self._case_memory is None:
            raise ValueError("build_for_case requires a CaseMemory - use build() with pre-fetched results instead")
        historical = self._case_memory.retrieve_similar(case_record, limit=self._limits.top_k_historical_cases)
        patterns = self._case_memory.detect_recurring_patterns()
        return self.build(
            snapshot, assessment,
            historical_cases=historical, recurring_patterns=patterns,
            policy_decision=policy_decision, case_id=case_record.case_id,
        )

    # ------------------------------------------------------------ pure entry point

    def build(
        self,
        snapshot: InvestigationSnapshot,
        assessment: UncertaintyAssessment,
        *,
        historical_cases: list[SimilarCaseResult] | None = None,
        recurring_patterns: list[RecurringPattern] | None = None,
        policy_decision: PolicyDecision | None = None,
        case_id: str | None = None,
    ) -> InvestigationContext:
        """Pure - takes already-retrieved historical results as plain
        arguments, so it never needs a `CaseMemory`/`CaseStore` at all.
        This is what makes offline unit tests possible with zero
        TigerGraph or CaseStore objects (Phase 2I §O)."""
        current_evidence = self._build_current_facts(snapshot)
        missing_information = self._build_missing_information(assessment)
        historical_items = self._build_historical_items(historical_cases or [])
        pattern_items = self._build_pattern_items(recurring_patterns or [])

        historical_items = historical_items[: self._limits.top_k_historical_cases]
        pattern_items = pattern_items[: self._limits.top_k_recurring_patterns]

        current_evidence, missing_information, historical_items, pattern_items, truncated, notes = (
            self._apply_size_limit(current_evidence, missing_information, historical_items, pattern_items)
        )

        context_items = [*current_evidence, *historical_items, *pattern_items, *missing_information]

        return InvestigationContext(
            transaction_id=snapshot.transaction_id,
            investigation_id=snapshot.investigation_id,
            case_id=case_id,
            generated_at=self._clock(),
            current_evidence=current_evidence,
            historical_cases=historical_items,
            recurring_patterns=pattern_items,
            missing_information=missing_information,
            context_items=context_items,
            uncertainty_assessment=assessment,
            policy_decision=policy_decision,
            truncated=truncated,
            truncation_notes=notes,
        )

    # ------------------------------------------------------------ current facts

    def _build_current_facts(self, snapshot: InvestigationSnapshot) -> list[ContextItem]:
        bundle = snapshot.evidence_bundle
        if bundle is None:
            return []
        items = []
        for e in bundle.evidence:
            if e.status not in (QueryStatus.SUCCESS, QueryStatus.EMPTY) or not e.observation:
                continue  # ERROR-status evidence has no grounded fact to state - see missing_information instead
            items.append(
                ContextItem(
                    context_id=self._id_generator(),
                    context_type=ContextItemType.CURRENT_FACT,
                    content=e.observation,
                    source="phase_2b_evidence_model",
                    provenance=(
                        f"Evidence {e.evidence_id} (type={e.evidence_type.value}, status={e.status.value}) "
                        f"from investigation {snapshot.investigation_id}"
                    ),
                    evidence_ids=[e.evidence_id],
                    case_ids=[],
                    quality=e.quality,
                    relevance=None,
                )
            )
        # Deterministic order: evidence_id is itself deterministic
        # (Phase 2B), independent of dict/list iteration order upstream.
        items.sort(key=lambda item: item.evidence_ids[0])
        return items

    # ------------------------------------------------------------ missing information

    def _build_missing_information(self, assessment: UncertaintyAssessment) -> list[ContextItem]:
        items = []
        for m in sorted(assessment.missing_evidence, key=lambda m: m.evidence_type):
            items.append(
                ContextItem(
                    context_id=self._id_generator(),
                    context_type=ContextItemType.MISSING_INFORMATION,
                    content=m.impact,
                    source="uncertainty_engine",
                    provenance=f"UncertaintyAssessment.missing_evidence: {m.evidence_type} ({m.reason.value})",
                    evidence_ids=[],
                    case_ids=[],
                    quality=None,
                    relevance=None,
                )
            )
        return items

    # ------------------------------------------------------------ historical cases

    def _build_historical_items(self, historical_cases: list[SimilarCaseResult]) -> list[ContextItem]:
        items = []
        for h in historical_cases:  # already deterministically ordered by CaseMemory (Phase 2G)
            action = h.previous_actions[-1].action.value if h.previous_actions else "none recorded"
            outcome = h.outcome.outcome_type.value if h.outcome else "not recorded"
            features = ", ".join(h.matching_features) if h.matching_features else "no matching features recorded"
            content = (
                f"Historical case {h.case_id} (similarity={h.similarity_score:.2f}): matched on {features}. "
                f"Previous recommended action: {action}. Recorded outcome: {outcome}."
            )
            items.append(
                ContextItem(
                    context_id=self._id_generator(),
                    context_type=ContextItemType.HISTORICAL_CASE,
                    content=content,
                    source="case_memory",
                    provenance=f"CaseMemory.retrieve_similar: case_id={h.case_id}, similarity_score={h.similarity_score}",
                    evidence_ids=[],
                    case_ids=[h.case_id],
                    quality=None,
                    relevance=h.similarity_score,
                )
            )
        return items

    # ------------------------------------------------------------ recurring patterns

    def _build_pattern_items(self, patterns: list[RecurringPattern]) -> list[ContextItem]:
        items = []
        for p in patterns:  # already deterministically ordered by CaseMemory (Phase 2G)
            actions = ", ".join(sorted({a.value for a in p.historical_actions})) or "none recorded"
            outcomes = ", ".join(sorted({o.value for o in p.historical_outcomes})) or "none recorded"
            content = (
                f"Recurring evidence pattern across {p.occurrences} case(s): {', '.join(p.evidence_types)}. "
                f"Related cases: {', '.join(p.related_case_ids)}. "
                f"Historical actions observed: {actions}. Historical outcomes observed: {outcomes}."
            )
            items.append(
                ContextItem(
                    context_id=self._id_generator(),
                    context_type=ContextItemType.DERIVED_PATTERN,
                    content=content,
                    source="case_memory_pattern_detection",
                    provenance=f"CaseMemory.detect_recurring_patterns: pattern_id={p.pattern_id}, occurrences={p.occurrences}",
                    evidence_ids=[],
                    case_ids=list(p.related_case_ids),
                    quality=None,
                    relevance=float(p.occurrences),
                )
            )
        return items

    # ------------------------------------------------------------ size limit

    def _apply_size_limit(
        self,
        current_evidence: list[ContextItem],
        missing_information: list[ContextItem],
        historical_items: list[ContextItem],
        pattern_items: list[ContextItem],
    ) -> tuple[list[ContextItem], list[ContextItem], list[ContextItem], list[ContextItem], bool, list[str]]:
        """Current-transaction facts (CURRENT_FACT, MISSING_INFORMATION)
        are never truncated - they are what the agent needs to know
        about the transaction actually being investigated. If the
        overall budget is exceeded, HISTORICAL_CASE and DERIVED_PATTERN
        items are trimmed first, deterministically (keep the
        highest-relevance-ranked prefix - both lists already arrive
        sorted by relevance descending)."""
        budget = self._limits.max_context_items
        reserved = len(current_evidence) + len(missing_information)
        remaining = max(0, budget - reserved)

        kept_historical = historical_items[:remaining]
        remaining_after_historical = max(0, remaining - len(kept_historical))
        kept_patterns = pattern_items[:remaining_after_historical]

        notes: list[str] = []
        truncated = False
        if len(kept_historical) < len(historical_items):
            truncated = True
            notes.append(
                f"historical_cases truncated from {len(historical_items)} to {len(kept_historical)} "
                f"(max_context_items={budget})"
            )
        if len(kept_patterns) < len(pattern_items):
            truncated = True
            notes.append(
                f"recurring_patterns truncated from {len(pattern_items)} to {len(kept_patterns)} "
                f"(max_context_items={budget})"
            )

        return current_evidence, missing_information, kept_historical, kept_patterns, truncated, notes
