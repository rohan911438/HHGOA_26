"""Phase 2D - the deterministic investigation service.

    InvestigationService.investigate_transaction(transaction_id, client)
            v
    get_transaction_context (always first, blocking - see §1 below)
            v
    ToolRegistry.execute() for the remaining five tools
    (sequential by default; bounded-concurrency opt-in - see §2)
            v
    Evidence items already produced by Phase 2B normalizers
            v
    app.evidence.aggregate.deduplicate_evidence + build_summary -> EvidenceBundle
            v
    InvestigationSnapshot

This module only imports `app.investigation.registry` for TigerGraph
access - never `app.tigergraph.queries` or `app.tigergraph.client`
directly, and never an LLM. No fraud verdict is computed or stored
anywhere in this file; see app/investigation/snapshot.py's docstring.

## 1. Why transaction context runs first, and blocks

`get_transaction_context` establishes that the transaction exists at
all. If it errors (including NOT_FOUND), five more TigerGraph calls
against a transaction that may not even exist would be pointless load -
so the investigation stops there with status FAILED, and the remaining
tools never run.

## 2. Concurrency

`investigate_transaction(..., concurrent=False)` is the default -
sequential execution, one tool at a time. `concurrent=True` runs the
five non-context tools on a bounded `ThreadPoolExecutor`
(`max_concurrent_tools`, default 3 - conservative, never all five at
once, and configurable per `InvestigationService` instance). Regardless
of which tool finishes first, the snapshot's `tools_executed` /
`tool_results` / `coverage.per_tool_status` always follow the fixed
`INVESTIGATION_PLAN` order - result assembly iterates that order, never
completion order.

## 3. Timeouts compose: overall -> per-tool

An optional overall investigation timeout (`overall_timeout_seconds`)
bounds the whole call. Each individual tool still has its own timeout
via `ToolRegistry.execute()` (`app/investigation/registry.py`); when an
overall budget is set, each tool's own timeout is additionally capped to
whatever budget remains, so one slow tool cannot silently consume the
entire investigation's time. A tool that does not get to run (or does
not finish) because the overall budget ran out is recorded as a skipped
tool with an `INVESTIGATION_TIMEOUT` warning - the investigation still
returns a snapshot, never hangs.

## 4. No retries

A tool that fails is reported as ERROR with its real `ToolErrorType` -
never silently retried. TigerGraph can be genuinely, temporarily
unavailable (e.g. a Savanna workspace resuming); retrying automatically
here would multiply load during exactly the situation where that is
least wanted. See docs/phase-2-investigation-service.md.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from datetime import UTC, datetime

from app.evidence.aggregate import (
    build_summary,
    dataset_risk_score_from_is_fraud_label,
    deduplicate_evidence,
)
from app.evidence.models import EvidenceBundle, QueryStatus
from app.investigation.registry import ToolRegistry
from app.investigation.schemas import InvestigationToolResult
from app.investigation.snapshot import (
    INVESTIGATION_PLAN,
    CoverageSummary,
    InvestigationSnapshot,
    InvestigationStatus,
    ToolExecution,
)
from app.logging import InvestigationEvent, get_logger, log_event
from app.tigergraph.client import TigerGraphClient

logger = get_logger(__name__)

IdGenerator = Callable[[], str]
Clock = Callable[[], datetime]

_CONTEXT_TOOL = "get_transaction_context"
_REMAINING_TOOLS: tuple[str, ...] = tuple(name for name in INVESTIGATION_PLAN if name != _CONTEXT_TOOL)

# Conservative default - not all five remaining tools at once (Phase 2D
# §7/§18/§19). Configurable per InvestigationService instance, never
# hardcoded to an aggressive value.
DEFAULT_MAX_CONCURRENT_TOOLS = 3


def _default_id_generator() -> str:
    # investigation_id is a distinct namespace from evidence_id /
    # transaction_id (Phase 2D §15) - the "inv-" prefix makes that
    # visible at a glance, not just by convention.
    return f"inv-{uuid.uuid4().hex}"


def _default_clock() -> datetime:
    return datetime.now(UTC)


class InvestigationService:
    """Runs the fixed six-tool investigation plan through a ToolRegistry
    and assembles the result into a deterministic InvestigationSnapshot.

    `id_generator` and `clock` are injectable so tests never depend on
    `time.sleep()`, the real wall clock, or non-deterministic IDs (Phase
    2D §15/§16) - production code simply uses the defaults.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        max_concurrent_tools: int = DEFAULT_MAX_CONCURRENT_TOOLS,
        default_overall_timeout_seconds: float | None = None,
        id_generator: IdGenerator = _default_id_generator,
        clock: Clock = _default_clock,
    ) -> None:
        if max_concurrent_tools < 1:
            raise ValueError("max_concurrent_tools must be at least 1")
        self._registry = registry
        self._max_concurrent_tools = max_concurrent_tools
        self._default_overall_timeout_seconds = default_overall_timeout_seconds
        self._id_generator = id_generator
        self._clock = clock

    # ------------------------------------------------------------ entry point

    def investigate_transaction(
        self,
        transaction_id: str,
        client: TigerGraphClient,
        *,
        concurrent: bool = False,
        overall_timeout_seconds: float | None = None,
    ) -> InvestigationSnapshot:
        investigation_id = self._id_generator()
        started_at = self._clock()
        overall_timeout = (
            overall_timeout_seconds
            if overall_timeout_seconds is not None
            else self._default_overall_timeout_seconds
        )

        log_event(
            logger,
            InvestigationEvent.CASE_STARTED,
            "investigation started",
            investigation_id=investigation_id,
            transaction_id=transaction_id,
        )

        cache: dict[str, InvestigationToolResult] = {}
        execution_log: list[ToolExecution] = []
        warnings: list[str] = []

        ctx_started = self._clock()
        ctx_result = self._execute_one(_CONTEXT_TOOL, transaction_id, client, cache, timeout_seconds=None)
        execution_log.append(self._to_execution_record(_CONTEXT_TOOL, ctx_result, ctx_started, self._clock()))

        if ctx_result.status == QueryStatus.ERROR:
            warnings.append(self._failure_warning(_CONTEXT_TOOL, ctx_result))
            return self._build_snapshot(
                investigation_id,
                transaction_id,
                started_at,
                self._clock(),
                tool_results={_CONTEXT_TOOL: ctx_result},
                executed_plan=[_CONTEXT_TOOL],
                execution_log=execution_log,
                warnings=warnings,
                status=InvestigationStatus.FAILED,
            )

        tool_results: dict[str, InvestigationToolResult] = {_CONTEXT_TOOL: ctx_result}

        remaining_budget = None
        if overall_timeout is not None:
            elapsed = (self._clock() - started_at).total_seconds()
            remaining_budget = max(0.0, overall_timeout - elapsed)

        if remaining_budget is not None and remaining_budget <= 0:
            skipped = list(_REMAINING_TOOLS)
        else:
            run = self._execute_concurrent if concurrent else self._execute_sequential
            executed, skipped, batch_log = run(transaction_id, client, cache, remaining_budget)
            tool_results.update(executed)
            execution_log.extend(batch_log)

        for name in skipped:
            warnings.append(f"INVESTIGATION_TIMEOUT: {name} did not complete - overall timeout reached.")
        for name, result in tool_results.items():
            if name != _CONTEXT_TOOL and result.status == QueryStatus.ERROR:
                warnings.append(self._failure_warning(name, result))

        executed_plan = [name for name in INVESTIGATION_PLAN if name in tool_results]
        status = self._determine_status(tool_results, executed_plan)

        # _execute_concurrent appends to execution_log in completion
        # order, not plan order - re-sort here so the snapshot's audit
        # trail is exactly as deterministic as tools_executed/tool_results
        # (Phase 2D §18: "preserve result ordering... regardless of
        # execution completion order"). Caught by this phase's own
        # TestDeterministicOrdering test before this fix.
        plan_index = {name: i for i, name in enumerate(INVESTIGATION_PLAN)}
        execution_log.sort(key=lambda entry: plan_index.get(entry.tool_name, len(plan_index)))

        return self._build_snapshot(
            investigation_id,
            transaction_id,
            started_at,
            self._clock(),
            tool_results=tool_results,
            executed_plan=executed_plan,
            execution_log=execution_log,
            warnings=warnings,
            status=status,
        )

    # ------------------------------------------------------------ execution

    def _execute_one(
        self,
        tool_name: str,
        transaction_id: str,
        client: TigerGraphClient,
        cache: dict[str, InvestigationToolResult],
        *,
        timeout_seconds: float | None,
    ) -> InvestigationToolResult:
        # Duplicate prevention (Phase 2D §20): the plan never calls the
        # same tool twice for one investigation, but this guards that
        # invariant explicitly rather than relying on caller discipline.
        if tool_name in cache:
            return cache[tool_name]
        kwargs = {} if timeout_seconds is None else {"timeout_seconds": timeout_seconds}
        result = self._registry.execute(tool_name, {"transaction_id": transaction_id}, client, **kwargs)
        cache[tool_name] = result
        return result

    def _execute_sequential(
        self,
        transaction_id: str,
        client: TigerGraphClient,
        cache: dict[str, InvestigationToolResult],
        remaining_budget: float | None,
    ) -> tuple[dict[str, InvestigationToolResult], list[str], list[ToolExecution]]:
        executed: dict[str, InvestigationToolResult] = {}
        skipped: list[str] = []
        log: list[ToolExecution] = []
        budget = remaining_budget

        for name in _REMAINING_TOOLS:
            if budget is not None and budget <= 0:
                skipped.append(name)
                continue
            call_started = self._clock()
            result = self._execute_one(name, transaction_id, client, cache, timeout_seconds=budget)
            log.append(self._to_execution_record(name, result, call_started, self._clock()))
            executed[name] = result
            if budget is not None:
                budget = max(0.0, budget - (result.latency_ms / 1000.0))

        return executed, skipped, log

    def _execute_concurrent(
        self,
        transaction_id: str,
        client: TigerGraphClient,
        cache: dict[str, InvestigationToolResult],
        remaining_budget: float | None,
    ) -> tuple[dict[str, InvestigationToolResult], list[str], list[ToolExecution]]:
        executed: dict[str, InvestigationToolResult] = {}
        skipped: list[str] = []
        log: list[ToolExecution] = []

        batch_started = self._clock()
        # Bounded (max_concurrent_tools workers, never "one thread per
        # tool" unconditionally) - Phase 2D §7/§19's load-protection
        # requirement.
        executor = ThreadPoolExecutor(max_workers=self._max_concurrent_tools)
        futures = {
            executor.submit(
                self._execute_one, name, transaction_id, client, cache, timeout_seconds=remaining_budget
            ): name
            for name in _REMAINING_TOOLS
        }
        done, not_done = futures_wait(futures.keys(), timeout=remaining_budget)

        for future in done:
            name = futures[future]
            result = future.result()
            executed[name] = result
            log.append(self._to_execution_record(name, result, batch_started, self._clock()))
        for future in not_done:
            skipped.append(futures[future])

        # Same reasoning as app.investigation.registry.ToolRegistry.execute:
        # do not block the caller waiting for an already-overdue
        # background call to finish - detach instead.
        executor.shutdown(wait=False)
        return executed, skipped, log

    # ------------------------------------------------------------ assembly

    @staticmethod
    def _failure_warning(tool_name: str, result: InvestigationToolResult) -> str:
        if result.error is None:
            return f"{tool_name} failed with an unclassified error."
        return f"{tool_name} failed ({result.error.error_type.value}): {result.error.message}"

    @staticmethod
    def _to_execution_record(
        tool_name: str,
        result: InvestigationToolResult,
        started_at: datetime,
        completed_at: datetime,
    ) -> ToolExecution:
        result_count = 0
        if result.evidence is not None:
            result_count = result.evidence.metrics.get(
                "related_transaction_count",
                result.evidence.metrics.get(
                    "total_related_not_deduplicated", len(result.evidence.related_entities)
                ),
            )
        return ToolExecution(
            tool_name=tool_name,
            status=result.status,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=result.latency_ms,
            result_count=result_count,
            error_type=result.error.error_type if result.error else None,
        )

    @staticmethod
    def _determine_status(
        tool_results: dict[str, InvestigationToolResult], executed_plan: list[str]
    ) -> InvestigationStatus:
        """Phase 2D §12's documented logic, exactly:

        FAILED    - get_transaction_context itself errored. Handled by an
                    early return in investigate_transaction and never
                    reaches this function - listed for completeness.
        PARTIAL   - context succeeded, but not every planned tool ran
                    (overall timeout), or at least one that did run
                    ended ERROR.
        COMPLETED - context succeeded and every planned tool completed
                    as SUCCESS or EMPTY.
        """
        if len(executed_plan) < len(INVESTIGATION_PLAN):
            return InvestigationStatus.PARTIAL
        if any(result.status == QueryStatus.ERROR for result in tool_results.values()):
            return InvestigationStatus.PARTIAL
        return InvestigationStatus.COMPLETED

    @staticmethod
    def _build_coverage(
        tool_results: dict[str, InvestigationToolResult], executed_plan: list[str]
    ) -> CoverageSummary:
        per_tool_status = {name: tool_results[name].status for name in executed_plan}
        return CoverageSummary(
            per_tool_status=per_tool_status,
            successful_tools=sum(1 for s in per_tool_status.values() if s == QueryStatus.SUCCESS),
            empty_tools=sum(1 for s in per_tool_status.values() if s == QueryStatus.EMPTY),
            failed_tools=sum(1 for s in per_tool_status.values() if s == QueryStatus.ERROR),
            total_tools=len(executed_plan),
        )

    def _build_snapshot(
        self,
        investigation_id: str,
        transaction_id: str,
        started_at: datetime,
        completed_at: datetime,
        *,
        tool_results: dict[str, InvestigationToolResult],
        executed_plan: list[str],
        execution_log: list[ToolExecution],
        warnings: list[str],
        status: InvestigationStatus,
    ) -> InvestigationSnapshot:
        # Reuses Phase 2B's own aggregation building blocks
        # (deduplicate_evidence + build_summary) on the Evidence items
        # the registry already produced - never re-queries TigerGraph
        # through aggregate_evidence a second time (Phase 2D §13/§19).
        evidence_items = [r.evidence for r in tool_results.values() if r.evidence is not None]
        deduped = deduplicate_evidence(evidence_items)

        ctx_result = tool_results.get(_CONTEXT_TOOL)
        ctx_evidence = ctx_result.evidence if ctx_result else None
        is_fraud_label = ctx_evidence.metrics.get("is_fraud_label") if ctx_evidence is not None else None
        dataset_risk_score = dataset_risk_score_from_is_fraud_label(is_fraud_label)

        summary = build_summary(transaction_id, dataset_risk_score, deduped)
        evidence_bundle = EvidenceBundle(
            transaction_id=transaction_id,
            dataset_risk_score=dataset_risk_score,
            evidence=deduped,
            evidence_summary=summary,
            data_quality_notes=summary.quality_notes,
        )

        log_event(
            logger,
            InvestigationEvent.CASE_COMPLETED,
            "investigation finished",
            investigation_id=investigation_id,
            transaction_id=transaction_id,
            status=status.value,
            tools_executed=len(executed_plan),
        )

        return InvestigationSnapshot(
            investigation_id=investigation_id,
            transaction_id=transaction_id,
            started_at=started_at,
            completed_at=completed_at,
            transaction_context=ctx_evidence,
            evidence_bundle=evidence_bundle,
            tools_executed=executed_plan,
            tool_results=tool_results,
            execution_log=execution_log,
            coverage=self._build_coverage(tool_results, executed_plan),
            warnings=warnings,
            status=status,
        )
