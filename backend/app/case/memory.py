"""Phase 2G - case memory: deterministic historical case retrieval.

**PROJECT DEVELOPMENT HEURISTIC - NOT an official HHGoa similarity
model, retrieval system, or fraud-typology classifier.** No vector
database, no embeddings, no LLM - a transparent, documented, weighted
structured-feature comparison over already-stored `CaseRecord`s.

`CaseMemory` operates entirely on `CaseRecord`s already saved to a
`CaseStore` - it never calls TigerGraph and never re-derives facts about
the *current* transaction/network (that is TigerGraph's and the
Phase 2A-2D pipeline's job). This is a deliberate architectural boundary
(Phase 2G §12): TigerGraph holds current graph facts, `CaseMemory` holds
historical investigation knowledge, and the two are never conflated -
important for the later GraphRAG/agent architecture this phase sets up
for, but does not itself build.

`CaseMemory` never concludes anything from a retrieved case's outcome -
it only exposes `(similarity_score, matching_features, outcome)` for
each result. Whatever consumes this (a future agent) decides what, if
anything, to do with that information; `CaseMemory` itself never states
or implies "therefore the current transaction is fraud."

## Similarity formula

```
similarity = (
    0.4 * jaccard(evidence_types_a, evidence_types_b)
  + 0.2 * (1.0 if uncertainty_level_a == uncertainty_level_b else 0.0)
  + 0.2 * (1.0 if policy_action_a == policy_action_b else 0.0)
  + 0.1 * jaccard(conflict_types_a, conflict_types_b)
  + 0.1 * (1.0 if trigger_a == trigger_b else 0.0)
)
```

Weights are `SimilarityWeights`, constructor-injectable, must sum to
1.0 (validated). `evidence_types` and `conflict_types` Jaccard overlap
uses `CaseRecord.evidence_types` (Phase 2F's `material_evidence` concept
- SUCCESS-status, non-empty findings only, not every evidence type the
investigation merely *attempted*) and the `conflict_type` values on the
case's stored `UncertaintyAssessment.conflicting_evidence`. Two cases
with *no* evidence in common score `0.0` on that component, never `1.0`
("nothing in common" is not a match). `isFraud` / `dataset_risk_score`
is **never** a similarity feature - `CaseRecord` does not even carry
that field, so this is an architectural guarantee, not a promise (see
models.py's module docstring).

Retrieval ranking is deterministic: sorted by score descending, ties
broken by `case_id` ascending - never by dict/set iteration order.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.case.models import (
    CaseOutcomeType,
    CaseRecord,
    RecurringPattern,
    SimilarCaseResult,
)
from app.case.store import CaseStore
from app.policy.models import PolicyAction


@dataclass(frozen=True)
class SimilarityWeights:
    evidence_type_overlap: float = 0.4
    uncertainty_level_match: float = 0.2
    action_match: float = 0.2
    conflict_type_overlap: float = 0.1
    trigger_match: float = 0.1

    def __post_init__(self) -> None:
        total = (
            self.evidence_type_overlap
            + self.uncertainty_level_match
            + self.action_match
            + self.conflict_type_overlap
            + self.trigger_match
        )
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"SimilarityWeights must sum to 1.0, got {total}")


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
        return 0.0  # nothing in common (both empty) is never scored as a match
    return len(a & b) / len(union)


def _conflict_types(case: CaseRecord) -> set[str]:
    if case.uncertainty_assessment is None:
        return set()
    return {c.conflict_type.value for c in case.uncertainty_assessment.conflicting_evidence}


def compute_similarity(a: CaseRecord, b: CaseRecord, weights: SimilarityWeights) -> tuple[float, list[str]]:
    """Returns (score in [0,1], explanatory feature strings) - the exact
    formula documented in this module's docstring. Never reads
    dataset_risk_score/isFraud (CaseRecord has no such field)."""
    features: list[str] = []
    total = 0.0

    ev_a, ev_b = set(a.evidence_types), set(b.evidence_types)
    ev_overlap = _jaccard(ev_a, ev_b)
    total += weights.evidence_type_overlap * ev_overlap
    shared = sorted(ev_a & ev_b)
    features.append(f"evidence_type_overlap={ev_overlap:.2f} (shared: {shared or 'none'})")

    level_a = a.uncertainty_assessment.uncertainty_level if a.uncertainty_assessment else None
    level_b = b.uncertainty_assessment.uncertainty_level if b.uncertainty_assessment else None
    level_match = 1.0 if level_a is not None and level_a == level_b else 0.0
    total += weights.uncertainty_level_match * level_match
    features.append(
        f"uncertainty_level_match={level_match:.2f} "
        f"({level_a.value if level_a else 'unknown'} vs {level_b.value if level_b else 'unknown'})"
    )

    action_a = a.policy_decision.action if a.policy_decision else None
    action_b = b.policy_decision.action if b.policy_decision else None
    action_match = 1.0 if action_a is not None and action_a == action_b else 0.0
    total += weights.action_match * action_match
    features.append(
        f"action_match={action_match:.2f} "
        f"({action_a.value if action_a else 'none'} vs {action_b.value if action_b else 'none'})"
    )

    conflict_overlap = _jaccard(_conflict_types(a), _conflict_types(b))
    total += weights.conflict_type_overlap * conflict_overlap
    features.append(f"conflict_type_overlap={conflict_overlap:.2f}")

    trigger_match = 1.0 if a.trigger == b.trigger else 0.0
    total += weights.trigger_match * trigger_match
    features.append(f"trigger_match={trigger_match:.2f} ({a.trigger.value} vs {b.trigger.value})")

    return total, features


class CaseMemory:
    """Wraps the same `CaseStore` a `CaseManager` writes to - a
    `CaseMemory` and the `CaseManager` that created its cases should
    normally share one `CaseStore` instance, so newly-created cases are
    immediately retrievable."""

    def __init__(self, store: CaseStore, *, weights: SimilarityWeights | None = None) -> None:
        self._store = store
        self._weights = weights or SimilarityWeights()

    def store(self, case: CaseRecord) -> None:
        self._store.save(case)

    def retrieve_by_transaction(self, transaction_id: str) -> list[CaseRecord]:
        return self._store.list_by_transaction(transaction_id)

    def retrieve_by_evidence_pattern(self, evidence_types) -> list[CaseRecord]:
        wanted = {t.value if hasattr(t, "value") else t for t in evidence_types}
        return sorted(
            (c for c in self._store.list_all() if wanted <= set(c.evidence_types)),
            key=lambda c: c.case_id,
        )

    def retrieve_by_action(self, action: PolicyAction) -> list[CaseRecord]:
        return sorted(
            (
                c
                for c in self._store.list_all()
                if c.policy_decision is not None and c.policy_decision.action == action
            ),
            key=lambda c: c.case_id,
        )

    def retrieve_by_outcome(self, outcome_type: CaseOutcomeType) -> list[CaseRecord]:
        return sorted(
            (c for c in self._store.list_all() if c.outcome is not None and c.outcome.outcome_type == outcome_type),
            key=lambda c: c.case_id,
        )

    def retrieve_similar(
        self, case: CaseRecord, *, limit: int = 5, min_similarity: float = 0.0
    ) -> list[SimilarCaseResult]:
        """Empty memory (or a memory containing only `case` itself)
        returns an empty list - never an error (Phase 2G §L)."""
        scored: list[tuple[float, CaseRecord, list[str]]] = []
        for other in self._store.list_all():
            if other.case_id == case.case_id:
                continue
            score, features = compute_similarity(case, other, self._weights)
            if score < min_similarity:
                continue
            scored.append((score, other, features))

        # Deterministic: score descending, case_id ascending tie-break -
        # never dict/set iteration order.
        scored.sort(key=lambda item: (-item[0], item[1].case_id))

        return [
            SimilarCaseResult(
                case_id=other.case_id,
                similarity_score=round(score, 6),
                matching_features=features,
                relevant_findings=other.findings,
                previous_decisions=other.decisions,
                previous_actions=other.actions,
                outcome=other.outcome,
            )
            for score, other, features in scored[:limit]
        ]

    def detect_recurring_patterns(self, *, min_occurrences: int = 2) -> list[RecurringPattern]:
        """Groups stored cases by their exact `evidence_types` set.
        Called a "recurring evidence pattern" - never a "fraud pattern",
        since this project's synthetic test data does not establish that
        every occurrence was confirmed fraud."""
        groups: dict[tuple[str, ...], list[CaseRecord]] = {}
        for c in self._store.list_all():
            if not c.evidence_types:
                continue
            key = tuple(sorted(c.evidence_types))
            groups.setdefault(key, []).append(c)

        patterns = []
        for key, cases in groups.items():
            if len(cases) < min_occurrences:
                continue
            cases_sorted = sorted(cases, key=lambda c: c.case_id)
            patterns.append(
                RecurringPattern(
                    pattern_id="pattern:" + ",".join(key),
                    evidence_types=list(key),
                    occurrences=len(cases_sorted),
                    related_case_ids=[c.case_id for c in cases_sorted],
                    historical_actions=[c.policy_decision.action for c in cases_sorted if c.policy_decision],
                    historical_outcomes=[c.outcome.outcome_type for c in cases_sorted if c.outcome],
                )
            )

        # Deterministic: most-frequent first, pattern_id ascending tie-break.
        patterns.sort(key=lambda p: (-p.occurrences, p.pattern_id))
        return patterns
