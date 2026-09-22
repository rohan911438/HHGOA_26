"""Phase 2H - the agent orchestrator.

    Agent / LLM Orchestrator
            |
    +-------+---------+----------------+
    v                 v                v
InvestigationService  CaseMemory   PolicyEngine
    v                 v                v
TigerGraph         CaseStore     PolicyDecision
    +-----------------+----------------+
                      v
                CaseManager
                      v
                 CaseRecord

## What the LLM controls vs. what it never touches

The LLM (via `LLMClient.decide_next_step`) controls exactly one thing:
which of four fixed `LLMDecisionAction` values to take next, plus (for
`request_more_evidence`) which of four fixed `RequestedEvidenceType`
values to request. That is the entire LLM-controlled surface of this
graph. It never:

  - calls TigerGraph, the tool registry, or MCP directly - only
    `InvestigationService.investigate_transaction()` is ever called, by
    this orchestrator's own code, never by the LLM
  - computes `evidence_coverage`, `signal_quality`, `overall_uncertainty`,
    a similarity score, or policy authorization - `UncertaintyEngine`,
    `CaseMemory`, and `PolicyEngine` remain the sole, unmodified,
    deterministic authorities for those (Phase 2E/2F/2G, untouched)
  - mutates a `CaseRecord` directly - only `CaseManager`'s typed methods
    ever do, and only ever with `decision_type=SYSTEM_RECOMMENDATION`
    (`add_system_recommendation`), never `HUMAN_DECISION`
  - executes any real-world action - `PolicyDecision.executable` is
    always `False` (Phase 2F) and nothing in this module ever sets a
    `CaseAction.executed=True`

`PolicyEngine.evaluate()` runs identically regardless of what the LLM
decided during `decide_next_step` - it is a pure function of the
snapshot and assessment already on state, so no LLM output can change
the policy outcome, only how many iterations occur before it runs.

## Failure handling

`AgentStatus.FAILED` is reserved for a genuine, unhandled exception (a
`TigerGraphClient`/`InvestigationService` call raising, or the LLM call
raising/returning unparseable output) - never for a *gracefully* FAILED
`InvestigationSnapshot` (e.g. `NOT_FOUND`), because `UncertaintyEngine`
and `PolicyEngine` already handle that case correctly on their own
(Phase 2E §Gate 1 / Phase 2F §Gate 1: `UNKNOWN` uncertainty,
`NOT_ACTIONABLE`/`REQUEST_MORE_EVIDENCE` policy) - reusing that existing
resilience rather than duplicating "is this a failure" logic here. When
a snapshot itself is FAILED, this graph skips straight to
`policy_evaluation` without consulting the LLM at all (nothing an
"investigate more" loop could fix, and no point spending an LLM call on
it).

## Iteration and tool-call safety

`max_iterations` bounds how many `investigate -> assess_uncertainty ->
decide_next_step -> gather_evidence` loops can occur; `max_tool_calls`
independently bounds how many times `investigate_transaction()` is
actually invoked (1:1 with iterations in this phase's design, but
tracked separately for forward-compatibility). Both are constructor
parameters - never hardcoded. Hitting either limit sets
`status=LIMIT_REACHED` (never `COMPLETED`) and the run still proceeds to
`policy_evaluation`/`case_update` - `PolicyEngine`'s own deterministic
conservatism (Phase 2F) is the actual safety net for "not enough
evidence," not a fabricated verdict from this module.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langgraph.graph import END, StateGraph

from app.agent.llm import LLMClient, LLMDecisionAction, MalformedLLMOutputError
from app.agent.prompts import SYSTEM_PROMPT_VERSION
from app.agent.state import (
    AgentEventType,
    AgentInvestigationResult,
    AgentMessage,
    AgentState,
    AgentStatus,
    RequestedEvidence,
    RequestedEvidenceStatus,
    RequestedEvidenceType,
)
from app.case import CaseManager, CaseMemory, CaseStatus, CaseStore, CaseTrigger
from app.context import ContextBuilder, format_context_for_prompt
from app.investigation import InvestigationService, build_default_registry
from app.investigation.snapshot import InvestigationStatus
from app.policy import PolicyEngine
from app.tigergraph.client import TigerGraphClient
from app.uncertainty import UncertaintyEngine

Clock = Callable[[], datetime]
IdGenerator = Callable[[], str]


def _default_clock() -> datetime:
    return datetime.now(UTC)


def _default_request_id() -> str:
    return f"req-{uuid.uuid4().hex}"


def _route_or_end(next_node: str):
    def _route(state: AgentState) -> str:
        return END if state.get("status") == AgentStatus.FAILED else next_node

    return _route


class AgentOrchestrator:
    """Composes the existing deterministic services (Phase 2D-2G,
    unmodified) with an `LLMClient` (Phase 2H) into one LangGraph
    workflow. Never imports `app.tigergraph.queries` or the raw
    `ToolRegistry` for its own use beyond building the default
    `InvestigationService` - the agent's only TigerGraph-facing surface
    is `InvestigationService.investigate_transaction()`."""

    def __init__(
        self,
        client: TigerGraphClient,
        case_store: CaseStore,
        llm_client: LLMClient,
        *,
        investigation_service: InvestigationService | None = None,
        uncertainty_engine: UncertaintyEngine | None = None,
        policy_engine: PolicyEngine | None = None,
        case_manager: CaseManager | None = None,
        case_memory: CaseMemory | None = None,
        context_builder: ContextBuilder | None = None,
        max_iterations: int = 5,
        max_tool_calls: int | None = None,
        id_generator: IdGenerator = _default_request_id,
        clock: Clock = _default_clock,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if max_tool_calls is not None and max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        self._client = client
        self._investigation_service = investigation_service or InvestigationService(build_default_registry())
        self._uncertainty_engine = uncertainty_engine or UncertaintyEngine()
        self._policy_engine = policy_engine or PolicyEngine()
        self._case_manager = case_manager or CaseManager(case_store)
        self._case_memory = case_memory or CaseMemory(case_store)
        # Phase 2I - built pure (no CaseMemory needed at call time): this
        # orchestrator already retrieves historical cases/patterns itself
        # and passes them in, so ContextBuilder never makes its own
        # CaseMemory call here (avoiding a duplicate retrieval). Shares
        # this orchestrator's own id_generator/clock so a deterministic
        # orchestrator (injected IDs/clock) produces a fully
        # deterministic InvestigationContext too.
        self._context_builder = context_builder or ContextBuilder(clock=clock, id_generator=id_generator)
        self._llm = llm_client
        self._max_iterations = max_iterations
        self._max_tool_calls = max_tool_calls if max_tool_calls is not None else max_iterations
        self._id_generator = id_generator
        self._clock = clock
        self._graph = self._build_graph()

    # ------------------------------------------------------------ entry point

    def run(self, transaction_id: str, *, trigger: CaseTrigger = CaseTrigger.UNKNOWN) -> AgentInvestigationResult:
        initial: AgentState = {
            "case_id": None,
            "transaction_id": transaction_id,
            "trigger": trigger,
            "investigation_snapshot": None,
            "uncertainty_assessment": None,
            "policy_decision": None,
            "case_record": None,
            "retrieved_historical_cases": [],
            "context": None,
            "findings": [],
            "agent_messages": [],
            "requested_evidence": [],
            "iteration_count": 0,
            "max_iterations": self._max_iterations,
            "tool_call_count": 0,
            "max_tool_calls": self._max_tool_calls,
            "status": AgentStatus.RUNNING,
            "final_explanation": None,
            "error": None,
            "last_decision_action": None,
            "last_decision_evidence_type": None,
            "last_decision_reason": None,
        }
        final_state = self._graph.invoke(initial)
        return self._to_result(final_state)

    # ------------------------------------------------------------ graph

    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("initialize_case", self._node_initialize_case)
        graph.add_node("investigate", self._node_investigate)
        graph.add_node("assess_uncertainty", self._node_assess_uncertainty)
        graph.add_node("build_context", self._node_build_context)  # Phase 2I
        graph.add_node("decide_next_step", self._node_decide_next_step)
        graph.add_node("gather_evidence", self._node_gather_evidence)
        graph.add_node("policy_evaluation", self._node_policy_evaluation)
        graph.add_node("case_update", self._node_case_update)

        graph.set_entry_point("initialize_case")
        graph.add_edge("initialize_case", "investigate")
        graph.add_conditional_edges("investigate", self._route_after_investigate)
        graph.add_conditional_edges("assess_uncertainty", self._route_after_uncertainty)
        graph.add_conditional_edges("build_context", _route_or_end("decide_next_step"))
        graph.add_conditional_edges("decide_next_step", self._route_after_decision)
        graph.add_edge("gather_evidence", "investigate")
        graph.add_conditional_edges("policy_evaluation", _route_or_end("case_update"))
        graph.add_edge("case_update", END)
        return graph.compile()

    # ------------------------------------------------------------ nodes

    def _node_initialize_case(self, state: AgentState) -> dict:
        message = self._event(
            AgentEventType.AGENT_STARTED, f"Starting investigation for transaction {state['transaction_id']}"
        )
        return {"agent_messages": [*state.get("agent_messages", []), message]}

    def _node_investigate(self, state: AgentState) -> dict:
        messages = list(state.get("agent_messages", []))
        iteration = state.get("iteration_count", 0) + 1
        tool_calls = state.get("tool_call_count", 0)
        max_tool_calls = state.get("max_tool_calls", self._max_tool_calls)

        if tool_calls >= max_tool_calls:
            messages.append(self._event(AgentEventType.AGENT_FAILED, "max_tool_calls reached before investigating"))
            return {
                "status": AgentStatus.LIMIT_REACHED,
                "agent_messages": messages,
                "iteration_count": iteration,
            }

        messages.append(
            self._event(AgentEventType.TOOL_REQUESTED, f"investigate_transaction (iteration {iteration})")
        )
        try:
            snapshot = self._investigation_service.investigate_transaction(state["transaction_id"], self._client)
        except Exception as exc:  # noqa: BLE001 - normalized, never re-raised into a fabricated result
            messages.append(
                self._event(AgentEventType.AGENT_FAILED, f"Investigation failed: {type(exc).__name__}: {exc}")
            )
            return {
                "status": AgentStatus.FAILED,
                "error": f"investigation failed: {type(exc).__name__}: {exc}",
                "agent_messages": messages,
                "iteration_count": iteration,
                "tool_call_count": tool_calls + 1,
            }

        messages.append(
            self._event(
                AgentEventType.TOOL_COMPLETED,
                f"investigate_transaction completed (status={snapshot.status.value})",
                tools_executed=len(snapshot.tools_executed),
            )
        )
        findings = sorted({e.observation for e in snapshot.evidence_bundle.evidence if e.observation}) if (
            snapshot.evidence_bundle
        ) else []

        return {
            "investigation_snapshot": snapshot,
            "findings": findings,
            "agent_messages": messages,
            "iteration_count": iteration,
            "tool_call_count": tool_calls + 1,
        }

    def _node_assess_uncertainty(self, state: AgentState) -> dict:
        messages = list(state.get("agent_messages", []))
        snapshot = state["investigation_snapshot"]
        assessment = self._uncertainty_engine.assess(snapshot)
        messages.append(
            self._event(
                AgentEventType.UNCERTAINTY_ASSESSED,
                f"uncertainty_level={assessment.uncertainty_level.value}",
                overall_uncertainty=assessment.overall_uncertainty,
            )
        )

        case_id = state.get("case_id")
        case_record = state.get("case_record")
        historical = state.get("retrieved_historical_cases", [])

        if case_id is None:
            case_record = self._case_manager.create_case(
                snapshot, uncertainty_assessment=assessment, trigger=state["trigger"]
            )
            case_id = case_record.case_id
            historical = self._case_memory.retrieve_similar(case_record)
            messages.append(self._event(AgentEventType.CASE_UPDATED, f"case {case_id} created", case_id=case_id))

        return {
            "uncertainty_assessment": assessment,
            "case_id": case_id,
            "case_record": case_record,
            "retrieved_historical_cases": historical,
            "agent_messages": messages,
        }

    def _node_build_context(self, state: AgentState) -> dict:
        """Phase 2I - assembles the grounded `InvestigationContext` the
        LLM will actually see, from data already computed by this run
        (no new TigerGraph or CaseMemory-retrieval call beyond the
        recurring-pattern lookup, which is itself TigerGraph-free -
        Phase 2G). `policy_decision=None` here: policy has not run yet
        at this point in the loop - `_node_case_update` rebuilds the
        context once more, with the real `PolicyDecision`, for the final
        explanation."""
        messages = list(state.get("agent_messages", []))
        patterns = self._case_memory.detect_recurring_patterns()
        context = self._context_builder.build(
            state["investigation_snapshot"],
            state["uncertainty_assessment"],
            historical_cases=state.get("retrieved_historical_cases", []),
            recurring_patterns=patterns,
            policy_decision=None,
            case_id=state.get("case_id"),
        )
        messages.append(
            self._event(
                AgentEventType.CONTEXT_BUILT,
                f"context built: {len(context.context_items)} item(s), truncated={context.truncated}",
            )
        )
        return {"context": context, "agent_messages": messages}

    def _node_decide_next_step(self, state: AgentState) -> dict:
        messages = list(state.get("agent_messages", []))
        summary = format_context_for_prompt(state["context"])
        try:
            decision = self._llm.decide_next_step(summary)
        except MalformedLLMOutputError as exc:
            messages.append(self._event(AgentEventType.AGENT_FAILED, f"LLM returned a malformed decision: {exc}"))
            return {"status": AgentStatus.FAILED, "error": f"malformed LLM decision: {exc}", "agent_messages": messages}
        except Exception as exc:  # noqa: BLE001 - normalized, never fabricates a decision
            messages.append(self._event(AgentEventType.AGENT_FAILED, f"LLM call failed: {type(exc).__name__}: {exc}"))
            return {"status": AgentStatus.FAILED, "error": f"LLM decision failed: {exc}", "agent_messages": messages}

        wants_more = decision.action in (LLMDecisionAction.REQUEST_MORE_EVIDENCE, LLMDecisionAction.INVESTIGATE)
        limit_reached = wants_more and state.get("iteration_count", 0) >= state.get(
            "max_iterations", self._max_iterations
        )
        messages.append(
            self._event(
                AgentEventType.TOOL_REQUESTED,
                f"LLM decided: {decision.action.value}",
                reason=decision.reason,
                limit_reached=limit_reached,
            )
        )
        result: dict = {
            "agent_messages": messages,
            "last_decision_action": decision.action.value,
            "last_decision_evidence_type": decision.requested_evidence_type,
            "last_decision_reason": decision.reason,
        }
        if limit_reached:
            result["status"] = AgentStatus.LIMIT_REACHED
        return result

    def _node_gather_evidence(self, state: AgentState) -> dict:
        messages = list(state.get("agent_messages", []))
        assessment = state.get("uncertainty_assessment")
        ev_type = state.get("last_decision_evidence_type") or RequestedEvidenceType.ANALYST_REVIEW

        # Deterministically derived, not LLM-invented: the specific
        # uncertainty fact that motivated this request.
        if assessment is not None and assessment.missing_evidence:
            uncertainty_factor = f"missing_evidence: {assessment.missing_evidence[0].evidence_type}"
        elif assessment is not None:
            uncertainty_factor = f"evidence_coverage={assessment.evidence_coverage:.2f}"
        else:
            uncertainty_factor = "unknown"

        request = RequestedEvidence(
            request_id=self._id_generator(),
            requested_evidence_type=ev_type,
            reason=state.get("last_decision_reason") or "insufficient evidence per uncertainty assessment",
            uncertainty_factor=uncertainty_factor,
            approval_required=False,
            status=RequestedEvidenceStatus.REQUESTED,
            requested_at=self._clock(),
        )
        messages.append(
            self._event(AgentEventType.EVIDENCE_REQUESTED, f"requested {ev_type.value}", request_id=request.request_id)
        )
        return {
            "requested_evidence": [*state.get("requested_evidence", []), request],
            "agent_messages": messages,
        }

    def _node_policy_evaluation(self, state: AgentState) -> dict:
        messages = list(state.get("agent_messages", []))
        snapshot = state["investigation_snapshot"]
        assessment = state.get("uncertainty_assessment") or self._uncertainty_engine.assess(snapshot)
        decision = self._policy_engine.evaluate(snapshot, assessment)
        messages.append(
            self._event(
                AgentEventType.POLICY_EVALUATED,
                f"action={decision.action.value} approval_required={decision.approval_required}",
            )
        )
        return {"uncertainty_assessment": assessment, "policy_decision": decision, "agent_messages": messages}

    def _node_case_update(self, state: AgentState) -> dict:
        messages = list(state.get("agent_messages", []))
        case_id = state["case_id"]
        policy_decision = state["policy_decision"]

        case_record = self._case_manager.get_case(case_id)
        if case_record.status == CaseStatus.OPEN:
            case_record = self._case_manager.transition_status(case_id, CaseStatus.INVESTIGATING)

        case_record = self._case_manager.add_system_recommendation(case_id, policy_decision)
        case_record = self._case_manager.add_action_from_policy_decision(case_id, policy_decision)

        if case_record.status == CaseStatus.INVESTIGATING:
            if policy_decision.action.value == "REQUEST_MORE_EVIDENCE":
                case_record = self._case_manager.transition_status(case_id, CaseStatus.PENDING_EVIDENCE)
            else:
                case_record = self._case_manager.transition_status(case_id, CaseStatus.ACTION_RECOMMENDED)
                case_record = self._case_manager.transition_status(case_id, CaseStatus.PENDING_REVIEW)

        status = state.get("status", AgentStatus.RUNNING)
        if status == AgentStatus.RUNNING:
            status = AgentStatus.COMPLETED

        # Rebuild context including the now-available PolicyDecision -
        # the context built in `build_context` (if that node even ran;
        # a gracefully-FAILED snapshot skips straight here) never had
        # one, since policy had not been evaluated yet at that point.
        final_context = self._context_builder.build(
            state["investigation_snapshot"],
            state["uncertainty_assessment"],
            historical_cases=state.get("retrieved_historical_cases", []),
            recurring_patterns=self._case_memory.detect_recurring_patterns(),
            policy_decision=policy_decision,
            case_id=case_id,
        )
        summary = format_context_for_prompt(final_context)
        try:
            explanation = self._llm.generate_explanation(summary)
        except Exception as exc:  # noqa: BLE001 - a missing explanation is not a fabricated one
            explanation = None
            messages.append(
                self._event(AgentEventType.AGENT_FAILED, f"Explanation generation failed: {type(exc).__name__}: {exc}")
            )

        messages.append(self._event(AgentEventType.CASE_UPDATED, f"case {case_id} updated", status=case_record.status.value))
        messages.append(self._event(AgentEventType.AGENT_FINISHED, f"agent run finished with status {status.value}"))

        return {
            "case_record": case_record,
            "context": final_context,
            "status": status,
            "final_explanation": explanation,
            "agent_messages": messages,
        }

    # ------------------------------------------------------------ routing

    def _route_after_investigate(self, state: AgentState) -> str:
        status = state.get("status")
        if status == AgentStatus.FAILED:
            return END
        if status == AgentStatus.LIMIT_REACHED:
            # Only reachable on iteration >= 2 (max_tool_calls >= 1 is
            # enforced in __init__), so a prior snapshot/assessment
            # already exists in state - reuse it rather than reassessing.
            return "policy_evaluation"
        return "assess_uncertainty"

    def _route_after_uncertainty(self, state: AgentState) -> str:
        if state.get("status") == AgentStatus.FAILED:
            return END
        snapshot = state["investigation_snapshot"]
        if snapshot.status == InvestigationStatus.FAILED:
            # Nothing an "investigate more" loop could fix, and
            # PolicyEngine's own Gate 1 already handles this correctly -
            # no point spending an LLM call (or a context build) on it.
            return "policy_evaluation"
        return "build_context"

    def _route_after_decision(self, state: AgentState) -> str:
        status = state.get("status")
        if status == AgentStatus.FAILED:
            return END
        if status == AgentStatus.LIMIT_REACHED:
            return "policy_evaluation"

        action = state.get("last_decision_action")
        if action == LLMDecisionAction.REQUEST_MORE_EVIDENCE.value:
            return "gather_evidence"
        if action == LLMDecisionAction.INVESTIGATE.value:
            return "investigate"
        return "policy_evaluation"

    # ------------------------------------------------------------ helpers

    def _event(self, event: AgentEventType, message: str, **metadata: Any) -> AgentMessage:
        return AgentMessage(event=event, message=message, created_at=self._clock(), metadata=metadata)

    def _to_result(self, state: AgentState) -> AgentInvestigationResult:
        # status is set authoritatively by the nodes themselves
        # (_node_investigate, _node_decide_next_step, _node_case_update)
        # - no inference needed here.
        status = state.get("status", AgentStatus.RUNNING)

        snapshot = state.get("investigation_snapshot")
        assessment = state.get("uncertainty_assessment")
        policy = state.get("policy_decision")

        evidence_summary: dict[str, Any] = {}
        evidence: list = []
        if snapshot is not None and snapshot.evidence_bundle is not None:
            evidence_summary = {
                "evidence_counts": snapshot.evidence_bundle.evidence_summary.evidence_counts,
                "query_status": {k: v.value for k, v in snapshot.evidence_bundle.evidence_summary.query_status.items()},
            }
            evidence = list(snapshot.evidence_bundle.evidence)

        next_step = _describe_next_step(status, policy)

        return AgentInvestigationResult(
            case_id=state.get("case_id"),
            transaction_id=state["transaction_id"],
            investigation_status=status,
            findings=state.get("findings", []),
            evidence_summary=evidence_summary,
            evidence=evidence,
            uncertainty_assessment=assessment,
            policy_decision=policy,
            requested_evidence=state.get("requested_evidence", []),
            historical_case_context=state.get("retrieved_historical_cases", []),
            context=state.get("context"),
            final_explanation=state.get("final_explanation"),
            next_step=next_step,
            iterations=state.get("iteration_count", 0),
            tool_call_count=state.get("tool_call_count", 0),
            completed=status == AgentStatus.COMPLETED,
            agent_messages=state.get("agent_messages", []),
            error=state.get("error"),
        )


def _describe_next_step(status: AgentStatus, policy) -> str:
    """Deterministic, never LLM-generated - a short machine-derived
    description of what happens next, distinct from the LLM's prose
    `final_explanation`."""
    if status == AgentStatus.FAILED:
        return "Investigation incomplete due to a system failure - retry once the underlying issue is resolved."
    if status == AgentStatus.LIMIT_REACHED:
        return "Iteration limit reached before evidence was judged sufficient - case routed for human review."
    if policy is None:
        return "No policy decision was produced."
    if policy.approval_required:
        return f"Awaiting {policy.approval_route.value} approval for the recommended action ({policy.action.value})."
    return f"No approval required - recommended action is {policy.action.value}."


__all__ = ["SYSTEM_PROMPT_VERSION", "AgentOrchestrator"]
