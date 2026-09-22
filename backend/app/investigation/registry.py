"""Phase 2C - the investigation tool registry.

The security and architecture boundary between a future LangGraph agent
and TigerGraph:

    Agent
      v
    ToolRegistry.execute(name, input, client)
      v
    one InvestigationTool from ALLOWED_INVESTIGATION_TOOLS (tools.py)
      v
    Phase 2A query + Phase 2B normalizer
      v
    TigerGraph

The agent should only ever call `list_tools()` / `execute()` on a
registry built by `build_default_registry()`. It never imports
app.tigergraph.queries or app.tigergraph.client directly, and there is
no raw-GSQL tool registered anywhere for it to reach.

`execute()` never raises for a validation failure, a timeout, or a
TigerGraph error - each becomes a structured ERROR-status
InvestigationToolResult instead, per the Phase 2B "EMPTY is not ERROR"
distinction (app/evidence/models.py). It *does* raise
`ToolNotRegisteredError` for an unknown tool name - that is a boundary
violation (the caller asked for a tool outside the allowlist), not a
query outcome, so it is a programming error to be fixed by the caller,
not evidence to report on some transaction.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from pydantic import BaseModel, ValidationError

from app.evidence.models import QueryStatus
from app.evidence.normalize import error_evidence
from app.investigation.errors import ToolErrorType, classify_exception
from app.investigation.schemas import InvestigationToolResult, ToolError
from app.investigation.tools import ALLOWED_INVESTIGATION_TOOLS, InvestigationTool
from app.logging import InvestigationEvent, get_logger, log_event
from app.tigergraph.client import TigerGraphClient

logger = get_logger(__name__)

# Falls back to this only if the client's settings object has no
# tg_timeout_seconds (e.g. a test double) - the real default comes from
# app.config.Settings.tg_timeout_seconds, the project's existing
# TigerGraph timeout convention.
_FALLBACK_TIMEOUT_SECONDS = 60


class ToolNotRegisteredError(KeyError):
    """Raised by `get`/`execute` for a tool name outside the explicit
    allowlist."""


class ToolRegistry:
    """Holds the tools an agent is allowed to call. Tools are added only
    via `register()` - nothing here scans a module or an MCP server for
    callables to expose automatically."""

    def __init__(self) -> None:
        self._tools: dict[str, InvestigationTool] = {}

    def register(self, tool: InvestigationTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> InvestigationTool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotRegisteredError(
                f"'{name}' is not a registered investigation tool. "
                f"Available: {sorted(self._tools)}"
            ) from None

    def list_tools(self) -> list[dict]:
        """Metadata only - never TigerGraph connection details, never a
        credential. Sorted by name for a deterministic listing."""
        return [self._tools[name].metadata.as_dict() for name in sorted(self._tools)]

    def execute(
        self,
        name: str,
        raw_input: dict[str, Any] | BaseModel,
        client: TigerGraphClient,
        *,
        timeout_seconds: float | None = None,
    ) -> InvestigationToolResult:
        tool = self.get(name)  # ToolNotRegisteredError propagates - see module docstring
        started = time.perf_counter()
        payload = raw_input.model_dump() if isinstance(raw_input, BaseModel) else raw_input

        try:
            validated = tool.input_schema.model_validate(payload)
        except (ValidationError, TypeError) as exc:
            return self._validation_failure(tool, payload, exc, started)

        timeout = timeout_seconds or getattr(
            client.settings, "tg_timeout_seconds", _FALLBACK_TIMEOUT_SECONDS
        )

        log_event(
            logger,
            InvestigationEvent.TOOL_CALLED,
            f"executing {name}",
            tool_name=name,
            transaction_id=validated.transaction_id,
        )

        # Not a `with` block: ThreadPoolExecutor.__exit__ calls
        # shutdown(wait=True), which would block until the worker thread
        # finishes - defeating the timeout below (a slow synchronous
        # HTTP call can't be cancelled mid-flight; caught live in this
        # phase's own test suite before this comment was added). On a
        # timeout we detach with shutdown(wait=False) instead, so the
        # caller gets its bounded response time even though the orphaned
        # worker keeps running in the background until the call itself
        # returns or raises.
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(tool.run, client, validated)
        try:
            evidence = future.result(timeout=timeout)
        except FutureTimeoutError:
            executor.shutdown(wait=False)
            return self._timeout_failure(tool, validated.transaction_id, timeout, started)
        except Exception as exc:  # noqa: BLE001 - normalized below, never re-raised
            executor.shutdown(wait=False)
            return self._execution_failure(tool, client, validated.transaction_id, exc, started)
        else:
            executor.shutdown(wait=False)

        elapsed = _elapsed_ms(started)
        result_count = evidence.metrics.get(
            "related_transaction_count",
            evidence.metrics.get("total_related_not_deduplicated", len(evidence.related_entities)),
        )
        log_event(
            logger,
            InvestigationEvent.TOOL_CALLED,
            f"{name} completed",
            tool_name=name,
            transaction_id=validated.transaction_id,
            status=evidence.status.value,
            latency_ms=elapsed,
            result_count=result_count,
        )
        return InvestigationToolResult(
            tool_name=name,
            status=evidence.status,
            transaction_id=validated.transaction_id,
            evidence=evidence,
            summary=evidence.observation or "",
            provenance=evidence.provenance,
            latency_ms=elapsed,
            error=None,
        )

    # ------------------------------------------------------ failure paths

    def _validation_failure(
        self, tool: InvestigationTool, payload: Any, exc: Exception, started: float
    ) -> InvestigationToolResult:
        elapsed = _elapsed_ms(started)
        txn_id = payload.get("transaction_id") if isinstance(payload, dict) else None
        detail = str(exc) if isinstance(exc, ValidationError) else f"{type(exc).__name__}: {exc}"
        log_event(
            logger,
            InvestigationEvent.TOOL_FAILED,
            f"{tool.name} rejected invalid input",
            tool_name=tool.name,
            error_type=ToolErrorType.VALIDATION_ERROR.value,
            latency_ms=elapsed,
        )
        return InvestigationToolResult(
            tool_name=tool.name,
            status=QueryStatus.ERROR,
            transaction_id=str(txn_id) if txn_id is not None else "",
            evidence=None,
            summary=f"Invalid input for {tool.name}: {detail}",
            provenance=None,
            latency_ms=elapsed,
            error=ToolError(error_type=ToolErrorType.VALIDATION_ERROR, message=detail),
        )

    def _timeout_failure(
        self, tool: InvestigationTool, transaction_id: str, timeout: float, started: float
    ) -> InvestigationToolResult:
        elapsed = _elapsed_ms(started)
        message = f"{tool.name} exceeded the {timeout}s tool timeout."
        log_event(
            logger,
            InvestigationEvent.TOOL_FAILED,
            f"{tool.name} timed out",
            tool_name=tool.name,
            transaction_id=transaction_id,
            error_type=ToolErrorType.TIMEOUT.value,
            latency_ms=elapsed,
        )
        return InvestigationToolResult(
            tool_name=tool.name,
            status=QueryStatus.ERROR,
            transaction_id=transaction_id,
            evidence=None,
            summary=message,
            provenance=None,
            latency_ms=elapsed,
            error=ToolError(error_type=ToolErrorType.TIMEOUT, message=message),
        )

    def _execution_failure(
        self,
        tool: InvestigationTool,
        client: TigerGraphClient,
        transaction_id: str,
        exc: Exception,
        started: float,
    ) -> InvestigationToolResult:
        elapsed = _elapsed_ms(started)
        evidence = error_evidence(
            tool.metadata.primary_evidence_type, transaction_id, tool.metadata.source_query, exc
        )
        error_type, message = classify_exception(client.settings.tg_host, exc)
        log_event(
            logger,
            InvestigationEvent.TOOL_FAILED,
            f"{tool.name} failed",
            tool_name=tool.name,
            transaction_id=transaction_id,
            error_type=error_type.value,
            latency_ms=elapsed,
        )
        return InvestigationToolResult(
            tool_name=tool.name,
            status=QueryStatus.ERROR,
            transaction_id=transaction_id,
            evidence=evidence,
            summary=message,
            provenance=evidence.provenance,
            latency_ms=elapsed,
            error=ToolError(error_type=error_type, message=message),
        )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def build_default_registry() -> ToolRegistry:
    """The one registry a future LangGraph agent should be handed -
    populated only from the explicit allowlist, never by reflection."""
    registry = ToolRegistry()
    for tool in ALLOWED_INVESTIGATION_TOOLS:
        registry.register(tool)
    return registry
