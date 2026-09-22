"""Phase 2I - deterministic, versioned context formatting for the agent
prompt.

Never concatenates raw dictionaries ad hoc - builds one structured,
labeled, JSON-safe dict from an `InvestigationContext`, with an explicit
section per `ContextItemType` plus uncertainty/policy. Bump
`app.context.models.CONTEXT_FORMAT_VERSION` whenever this shape changes,
the same way `app.agent.prompts.SYSTEM_PROMPT_VERSION` is bumped for
prompt text changes.

Historical-case and pattern content is always placed under a section
explicitly labeled "informational only" - the prompt built on top of
this (`app.agent.prompts`) treats the whole formatted block as DATA, not
instructions (Phase 2I §19).
"""

from __future__ import annotations

from typing import Any

from app.context.models import ContextItem, InvestigationContext


def _serialize_items(items: list[ContextItem]) -> list[dict[str, Any]]:
    return [
        {
            "context_id": item.context_id,
            "content": item.content,
            "evidence_ids": item.evidence_ids,
            "case_ids": item.case_ids,
            "quality": item.quality.value if item.quality else None,
            "relevance": item.relevance,
        }
        for item in items
    ]


def format_context_for_prompt(context: InvestigationContext) -> dict[str, Any]:
    """The only view of `InvestigationContext` the LLM ever sees."""
    assessment = context.uncertainty_assessment
    policy = context.policy_decision

    return {
        "context_format_version": context.context_format_version,
        "transaction_id": context.transaction_id,
        "CURRENT_EVIDENCE": {
            "meaning": "Grounded facts about the current transaction, from the current investigation.",
            "items": _serialize_items(context.current_evidence),
        },
        "MISSING_INFORMATION": {
            "meaning": "Evidence sources that failed or could not be obtained - unknown, not confirmed absent.",
            "items": _serialize_items(context.missing_information),
        },
        "HISTORICAL_CASES": {
            "meaning": (
                "Prior recorded investigations, informational only. NEVER current facts. "
                "NEVER proof of the current transaction's outcome."
            ),
            "items": _serialize_items(context.historical_cases),
        },
        "RECURRING_PATTERNS": {
            "meaning": (
                "Deterministic aggregation across historical cases sharing an evidence pattern. "
                "NEVER a 'fraud pattern' claim - only a recurrence count."
            ),
            "items": _serialize_items(context.recurring_patterns),
        },
        "UNCERTAINTY": (
            {
                "evidence_coverage": assessment.evidence_coverage,
                "signal_quality": assessment.signal_quality,
                "signal_conflict": assessment.signal_conflict,
                "data_completeness": assessment.data_completeness,
                "overall_uncertainty": assessment.overall_uncertainty,
                "uncertainty_level": assessment.uncertainty_level.value,
                "sufficient_for_next_stage": assessment.sufficient_for_next_stage,
                "meaning": "Computed once by UncertaintyEngine (Phase 2E) - authoritative, never recalculate.",
            }
            if assessment is not None
            else None
        ),
        "POLICY_RESULT": (
            {
                "action": policy.action.value,
                "approval_required": policy.approval_required,
                "approval_route": policy.approval_route.value,
                "executable": policy.executable,
                "status": policy.status.value,
                "meaning": "Computed once by PolicyEngine (Phase 2F) - authoritative, never override.",
            }
            if policy is not None
            else None
        ),
        "truncated": context.truncated,
        "truncation_notes": context.truncation_notes,
    }
