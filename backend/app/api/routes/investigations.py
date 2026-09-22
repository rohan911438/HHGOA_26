"""Phase 2K - investigation endpoints.

Route handlers are thin: every one of them calls exactly one method on
an existing application-layer object (`AgentOrchestrator.run`,
`InvestigationRegistry.get`) and shapes the typed response. No
investigation/evidence/uncertainty/policy logic is duplicated here - see
`app/agent/orchestrator.py` for all of that.

`AgentOrchestrator.run()` is synchronous (it calls a synchronous
`TigerGraphClient` and a synchronous `LLMClient` under the hood - see
orchestrator.py). These route functions are declared `def`, not `async
def`, on purpose: FastAPI/Starlette runs a sync path-operation function
in an external threadpool automatically, which is the safe boundary here
- wrapping an already-synchronous, multi-second call in `async def` and
an `await` would not make it non-blocking, only pretend to (the explicit
instruction this phase gives against "fake async for appearance").
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.agent import AgentOrchestrator, AgentStatus
from app.api.dependencies import get_investigation_registry, get_orchestrator
from app.api.errors import AgentFailedError, InvestigationFailedError, NotFoundError
from app.api.models import InvestigationRequest, InvestigationResponse
from app.api.registry import InvestigationRegistry

router = APIRouter(tags=["investigations"])

# Substrings of AgentOrchestrator's own error messages (orchestrator.py's
# _node_decide_next_step) that distinguish an LLM-caused failure from a
# deterministic-backend failure - never re-classified by guessing, only
# by matching the exact strings that module already produces.
_AGENT_FAILURE_MARKERS = ("malformed LLM decision", "LLM decision failed")


def _investigation_id_for(result) -> str:
    return result.case_id or f"inv-{uuid.uuid4().hex}"


@router.post("/investigations", response_model=InvestigationResponse)
def start_investigation(
    body: InvestigationRequest,
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
    registry: InvestigationRegistry = Depends(get_investigation_registry),
) -> InvestigationResponse:
    result = orchestrator.run(body.transaction_id, trigger=body.trigger)

    investigation_id = _investigation_id_for(result)
    registry.put(investigation_id, result)

    if result.investigation_status == AgentStatus.FAILED:
        error_text = result.error or "investigation failed"
        if any(marker in error_text for marker in _AGENT_FAILURE_MARKERS):
            raise AgentFailedError(
                "The agent's LLM decision step failed.",
                details={"transaction_id": body.transaction_id, "investigation_id": investigation_id},
            )
        raise InvestigationFailedError(
            "The investigation pipeline failed before a result could be produced.",
            details={"transaction_id": body.transaction_id, "investigation_id": investigation_id},
        )

    return _to_response(result, investigation_id)


@router.get("/investigations/{investigation_id}", response_model=InvestigationResponse)
def get_investigation(
    investigation_id: str,
    registry: InvestigationRegistry = Depends(get_investigation_registry),
) -> InvestigationResponse:
    result = registry.get(investigation_id)
    if result is None:
        raise NotFoundError(
            f"Investigation '{investigation_id}' was not found. Note: this API does not "
            "persist investigation-level records separately from cases (see "
            "docs/phase-2-api.md) - only investigations run by this API process, since "
            "its last restart, are retrievable here.",
            details={"investigation_id": investigation_id},
        )
    return _to_response(result, investigation_id)


def _to_response(result, investigation_id: str) -> InvestigationResponse:
    return InvestigationResponse(
        investigation_id=investigation_id,
        case_id=result.case_id,
        transaction_id=result.transaction_id,
        status=result.investigation_status,
        completed=result.completed,
        iterations=result.iterations,
        tool_calls=result.tool_call_count,
        findings=result.findings,
        evidence=result.evidence,
        evidence_summary=result.evidence_summary,
        uncertainty=result.uncertainty_assessment,
        policy_decision=result.policy_decision,
        requested_evidence=result.requested_evidence,
        historical_context=result.historical_case_context,
        context=result.context,
        explanation=result.final_explanation,
        next_step=result.next_step,
        error=result.error,
    )
