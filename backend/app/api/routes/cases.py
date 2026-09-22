"""Phase 2K - case endpoints.

Every handler retrieves through the existing application layer
(`CaseManager.get_case`, `CaseMemory.retrieve_similar`) and reshapes the
result into a typed response - no case/evidence/similarity logic is
recomputed here. `GET /cases/{case_id}` returns the project's own
`CaseRecord` (Phase 2G, unmodified) directly as the response model: it
is already a frozen, JSON-serializable Pydantic model with a stable
public shape, so wrapping it in a second, API-only schema would only
duplicate its field list without adding anything.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import (
    get_case_manager,
    get_case_memory,
    get_case_store,
    get_investigation_registry,
)
from app.api.errors import NotFoundError
from app.api.models import (
    ContextResponse,
    EvidenceResponse,
    HistoryEvent,
    HistoryEventType,
    HistoryResponse,
    SimilarCasesResponse,
)
from app.api.registry import InvestigationRegistry
from app.case import CaseManager, CaseMemory, CaseNotFoundError, CaseStore
from app.case.models import CaseRecord

router = APIRouter(tags=["cases"])


def _get_case_or_404(case_id: str, case_manager: CaseManager) -> CaseRecord:
    try:
        return case_manager.get_case(case_id)
    except CaseNotFoundError as exc:
        raise NotFoundError(f"Case '{case_id}' was not found.", details={"case_id": case_id}) from exc


@router.get("/cases", response_model=list[CaseRecord])
def list_cases(case_store: CaseStore = Depends(get_case_store)) -> list[CaseRecord]:
    """Every case this API process has created or updated, via the same
    `CaseStore.list_all()` `CaseMemory` already uses internally - no
    second listing/filtering implementation. Sorted by `case_id`
    (`InMemoryCaseStore.list_all()`'s own deterministic order)."""
    return case_store.list_all()


@router.get("/cases/{case_id}", response_model=CaseRecord)
def get_case(case_id: str, case_manager: CaseManager = Depends(get_case_manager)) -> CaseRecord:
    return _get_case_or_404(case_id, case_manager)


@router.get("/cases/{case_id}/evidence", response_model=EvidenceResponse)
def get_case_evidence(
    case_id: str,
    case_manager: CaseManager = Depends(get_case_manager),
    registry: InvestigationRegistry = Depends(get_investigation_registry),
) -> EvidenceResponse:
    case = _get_case_or_404(case_id, case_manager)
    cached = registry.get(case_id)

    if cached is not None and cached.evidence:
        return EvidenceResponse(
            case_id=case.case_id,
            transaction_id=case.transaction_id,
            detail_available=True,
            evidence=cached.evidence,
            evidence_ids=case.evidence_ids,
            evidence_types=case.evidence_types,
        )

    return EvidenceResponse(
        case_id=case.case_id,
        transaction_id=case.transaction_id,
        detail_available=False,
        evidence_ids=case.evidence_ids,
        evidence_types=case.evidence_types,
        note=(
            "Full per-item evidence detail (observation/interpretation/provenance/quality) "
            "is only cached in this API process for investigations it ran itself - see "
            "docs/phase-2-api.md 'Known limitations'. Falling back to the compact "
            "evidence_ids/evidence_types this case's CaseRecord already carries."
        ),
    )


@router.get("/cases/{case_id}/history", response_model=HistoryResponse)
def get_case_history(case_id: str, case_manager: CaseManager = Depends(get_case_manager)) -> HistoryResponse:
    case = _get_case_or_404(case_id, case_manager)

    events: list[HistoryEvent] = [
        HistoryEvent(
            event_type=HistoryEventType.CASE_CREATED,
            occurred_at=case.created_at,
            description=f"Case created for transaction {case.transaction_id} (trigger: {case.trigger.value}).",
            ref_id=case.case_id,
        )
    ]
    for finding in case.findings:
        events.append(
            HistoryEvent(
                event_type=HistoryEventType.FINDING_ADDED,
                occurred_at=finding.created_at,
                description=finding.description,
                ref_id=finding.finding_id,
            )
        )
    for decision in case.decisions:
        event_type = {
            "SYSTEM_RECOMMENDATION": HistoryEventType.RECOMMENDATION_CREATED,
            "APPROVAL": HistoryEventType.APPROVAL_RECORDED,
        }.get(decision.decision_type.value, HistoryEventType.RECOMMENDATION_CREATED)
        events.append(
            HistoryEvent(
                event_type=event_type,
                occurred_at=decision.created_at,
                description=decision.rationale,
                ref_id=decision.decision_id,
            )
        )
    for action in case.actions:
        events.append(
            HistoryEvent(
                event_type=HistoryEventType.ACTION_RECORDED,
                occurred_at=action.created_at,
                description=f"{action.action.value} - {action.status.value} ({action.rationale})",
                ref_id=action.action_id,
            )
        )
    if case.outcome is not None:
        events.append(
            HistoryEvent(
                event_type=HistoryEventType.OUTCOME_RECORDED,
                occurred_at=case.outcome.recorded_at,
                description=(
                    f"{case.outcome.outcome_type.value}"
                    + (" (SYNTHETIC DEVELOPMENT CASE)" if case.outcome.is_synthetic else "")
                    + (f" - {case.outcome.notes}" if case.outcome.notes else "")
                ),
            )
        )

    events.sort(key=lambda e: e.occurred_at)

    return HistoryResponse(
        case_id=case.case_id,
        events=events,
        note=(
            "Derived only from timestamped CaseRecord fields (findings/decisions/actions/"
            "outcome). CaseRecord does not separately timestamp evidence-attachment or "
            "status-change events (only the current evidence_ids/status), so those event "
            "types are not included - see docs/phase-2-api.md 'Known limitations'."
        ),
    )


@router.get("/cases/{case_id}/similar", response_model=SimilarCasesResponse)
def get_similar_cases(
    case_id: str,
    limit: int = Query(default=5, ge=1, le=50),
    min_similarity: float = Query(default=0.0, ge=0.0, le=1.0),
    case_manager: CaseManager = Depends(get_case_manager),
    case_memory: CaseMemory = Depends(get_case_memory),
) -> SimilarCasesResponse:
    case = _get_case_or_404(case_id, case_manager)
    similar = case_memory.retrieve_similar(case, limit=limit, min_similarity=min_similarity)
    return SimilarCasesResponse(
        case_id=case.case_id,
        similar_cases=similar,
        note=(
            "Similarity is a PROJECT DEVELOPMENT HEURISTIC (app.case.memory.compute_similarity), "
            "not an official HHGoa system. Any outcome shown may be a SYNTHETIC DEVELOPMENT CASE "
            "(CaseOutcome.is_synthetic) - never a real bank case."
        ),
    )


@router.get("/cases/{case_id}/context", response_model=ContextResponse)
def get_case_context(
    case_id: str,
    case_manager: CaseManager = Depends(get_case_manager),
    registry: InvestigationRegistry = Depends(get_investigation_registry),
) -> ContextResponse:
    case = _get_case_or_404(case_id, case_manager)
    cached = registry.get(case_id)

    if cached is not None and cached.context is not None:
        return ContextResponse(case_id=case.case_id, available=True, context=cached.context)

    return ContextResponse(
        case_id=case.case_id,
        available=False,
        note=(
            "The grounded InvestigationContext (GraphRAG bundle) is only cached in this API "
            "process for investigations it ran itself - see docs/phase-2-api.md 'Known "
            "limitations'. Rebuilding it would require re-running the investigation, which "
            "this endpoint deliberately does not do."
        ),
    )
