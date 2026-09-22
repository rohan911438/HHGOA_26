"""Phase 2G - case management + case memory.

    InvestigationSnapshot + UncertaintyAssessment + PolicyDecision
            v
    CaseManager
            v
    CaseRecord
       /        \\
  CaseStore   CaseMemory
                   v
           Historical Cases

No TigerGraph, no LLM. Historical cases used anywhere in this package's
tests are SYNTHETIC DEVELOPMENT CASES - the official HHGoa case
dataset/policy artifacts are not available in the current IEEE-CIS
development fallback. CaseMemory's similarity/retrieval mechanism is a
PROJECT DEVELOPMENT HEURISTIC, not an official HHGoa system. See
docs/phase-2-case-management-memory.md.
"""

from __future__ import annotations

from app.case.manager import (
    LEGAL_TRANSITIONS,
    CaseActionNotFoundError,
    CaseManager,
    CaseNotFoundError,
    InvalidCaseTransitionError,
)
from app.case.memory import CaseMemory, SimilarityWeights, compute_similarity
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
    RecurringPattern,
    SimilarCaseResult,
)
from app.case.store import CaseStore, InMemoryCaseStore

__all__ = [
    "LEGAL_TRANSITIONS",
    "CaseAction",
    "CaseActionNotFoundError",
    "CaseActionStatus",
    "CaseDecision",
    "CaseFinding",
    "CaseManager",
    "CaseMemory",
    "CaseNotFoundError",
    "CaseOutcome",
    "CaseOutcomeType",
    "CaseRecord",
    "CaseStatus",
    "CaseStore",
    "CaseTrigger",
    "DecisionType",
    "InMemoryCaseStore",
    "InvalidCaseTransitionError",
    "RecurringPattern",
    "SimilarCaseResult",
    "SimilarityWeights",
    "compute_similarity",
]
