# Phase 2C — Investigation Tool Registry

Status: **COMPLETE**. Offline: 64/64 PASS. Live: 9/9 PASS. Full suite:
149/149 PASS. No LangGraph, no LLM, no frontend added this phase.

## 1. What this phase adds

A controlled tool layer between a future LangGraph agent and TigerGraph.
Nothing here queries TigerGraph or shapes evidence itself — it reuses
Phase 2A (`app/tigergraph/queries.py`) and Phase 2B
(`app/evidence/normalize.py`, `app/evidence/models.py`) unchanged, and
adds only the orchestration around them: input validation, timeouts,
error normalization, structured logging, and an explicit allowlist.

```
LangGraph Agent (future)
        |
        v
Investigation Tool Registry        (app/investigation/registry.py)
        |
        +--> get_transaction_context
        +--> find_shared_card_activity
        +--> find_shared_device_activity
        +--> find_shared_address_activity
        +--> find_shared_email_activity
        +--> investigate_transaction_network
        |
        v
Phase 2A query (app/tigergraph/queries.py)
        |
        v
Phase 2B normalizer (app/evidence/normalize.py) -> Evidence
        |
        v
TigerGraph
```

## 2. Package layout

```
app/investigation/
    __init__.py    re-exports ToolRegistry, build_default_registry, InvestigationToolResult, ToolError
    schemas.py      typed input schemas + the InvestigationToolResult/ToolError output contract
    tools.py        the six InvestigationTool definitions + ALLOWED_INVESTIGATION_TOOLS allowlist
    registry.py     ToolRegistry: register/get/list_tools/execute, timeouts, error normalization
    errors.py       ToolErrorType + classify_exception (reuses app.tigergraph.diagnostics.classify_network)
```

No duplication of the TigerGraph client, query functions, evidence
models, configuration, or logging — every one of those is imported from
its existing Phase 1/2A/2B/2B location.

## 3. The six registered tools

| Tool name | Wraps (Phase 2A) | Evidence type |
|---|---|---|
| `get_transaction_context` | `queries.get_transaction_context` | `transaction_context` |
| `find_shared_card_activity` | `queries.find_shared_card_activity` | `shared_card` |
| `find_shared_device_activity` | `queries.find_shared_device_activity` | `shared_device` |
| `find_shared_address_activity` | `queries.find_shared_address_activity` | `shared_address` |
| `find_shared_email_activity` | `queries.find_shared_email_activity` | `shared_email_domain` |
| `investigate_transaction_network` | `queries.investigate_transaction_network` | `network_pattern` |

Each tool's `description` (returned by `list_tools()`) says what evidence
it returns and, where relevant, its known signal-quality limitation
(Address: coarse regional codes; EmailDomain: very few distinct domains).
None claims the tool "detects fraud" — enforced by
`tests/unit/test_investigation_tools.py::TestAllowlistAndMetadata`.

`ALLOWED_INVESTIGATION_TOOLS` in `tools.py` is a hand-written tuple of
six `InvestigationTool` instances, built directly in that module. Nothing
in this package reflects over a module namespace or over the TigerGraph
MCP server's tool list to build this set.

## 4. Input schemas

Every tool takes exactly one field: `transaction_id`. `TransactionIdInput`
(app/investigation/schemas.py) defines the rule once; the six per-tool
subclasses (`TransactionContextInput`, `SharedCardInput`, ...) exist only
so each tool's JSON schema carries its own name — the shape is identical.

```python
TransactionId = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=128),
]
```

- `strict=True` — rejects a non-string `transaction_id` (e.g. an int)
  rather than silently coercing it.
- `strip_whitespace=True` + `min_length=1` — rejects `""`, `None`
  (via the required field), and whitespace-only strings.
- `max_length=128` — a sanity bound against obviously malformed input
  only. It does **not** assume the dataset's IDs are numeric or
  fixed-width; `"TXN-ABC-123"` validates fine.
- `extra="forbid"` — rejects unexpected additional fields.

## 5. Output contract

```python
class InvestigationToolResult(BaseModel):
    tool_name: str
    status: QueryStatus       # reused from app.evidence.models — SUCCESS / EMPTY / ERROR
    transaction_id: str
    evidence: Evidence | None  # reused from app.evidence.models, unchanged
    summary: str                # observation on success, error message on failure
    provenance: Provenance | None
    latency_ms: float
    error: ToolError | None     # present only when status is ERROR
```

`status` is exactly Phase 2B's `QueryStatus` — not a second, incompatible
vocabulary. `evidence` and `provenance` are the same `Evidence` /
`Provenance` Pydantic models Phase 2B already defines.

## 6. SUCCESS / EMPTY / ERROR — the distinction that must survive

- **EMPTY** (e.g. `find_shared_device_activity` on a transaction with no
  identity row) means the query ran fine and found nothing.
  `error` is always `None` on an EMPTY result — verified by
  `TestStatusPropagation::test_empty_status_propagates_and_is_not_an_error`.
- **ERROR** means the query could not be completed — the registry does
  not know whether related activity exists.

`ToolErrorType.EMPTY_RESULT` exists in the enum for completeness but is
never emitted by this layer, precisely to avoid blurring that line (see
the docstring in `errors.py`).

## 7. Error normalization

`app/investigation/errors.py::classify_exception(host, exc)` maps any
exception raised inside a tool call to one `ToolErrorType` plus a safe
message:

| Exception | ToolErrorType |
|---|---|
| Pydantic `ValidationError` (caught in `registry.execute`, before any TigerGraph call) | `VALIDATION_ERROR` |
| `TigerGraphQueryError("... does not exist")` | `NOT_FOUND` |
| any other `TigerGraphQueryError` | `QUERY_ERROR` |
| `TigerGraphUnavailable`, classified `AUTHENTICATION` by `diagnostics.classify_network` | `AUTHENTICATION_ERROR` |
| `TigerGraphUnavailable`, classified `NETWORK`/`SERVER`/`ENDPOINT`/`CONFIGURATION` | `TIGERGRAPH_UNAVAILABLE` |
| `TigerGraphUnavailable`, classified `SDK` | `INTERNAL_ERROR` |
| a `concurrent.futures.TimeoutError` from the tool timeout | `TIMEOUT` |
| anything else | `INTERNAL_ERROR` |

`classify_network` is the exact function Phase 2B's own health tooling
uses (`app/tigergraph/diagnostics.py`) — reused here rather than
re-derived, so the tool layer and the health CLI never disagree about
what a given TigerGraph failure means.

No credential value is ever placed in a `ToolError.message` — the
messages come from `TigerGraphClient`'s own wrapper text, which already
never embeds a secret's value (only the setting name, e.g. `TG_SECRET`).
Verified live in this phase's test run and by
`TestErrorNormalization::test_no_credential_shaped_text_appears_in_a_normalized_error`.

## 8. Timeouts

Every tool call runs on a one-shot `ThreadPoolExecutor(max_workers=1)`;
`registry.execute()` bounds the wait with `future.result(timeout=...)`,
defaulting to `Settings.tg_timeout_seconds` (the project's existing
TigerGraph timeout setting, default 60s) unless overridden per call.

**Real defect found and fixed while building this**: the first
implementation used `with ThreadPoolExecutor(...) as executor:`.
`ThreadPoolExecutor.__exit__` calls `shutdown(wait=True)`, which blocks
until the worker thread finishes — so a "timeout" only ever fired *after*
the slow call itself completed, not before. Fixed by managing the
executor manually and calling `shutdown(wait=False)` on both the timeout
and the exception paths, so `execute()` returns as soon as the timeout
elapses. The underlying synchronous HTTP call cannot be cancelled
mid-flight (a Python limitation, not specific to this project) and keeps
running in an orphaned thread until it finishes or errors — accepted
because these are read-only investigation queries, not mutations.
Regression test:
`TestErrorNormalization::test_timeout_becomes_a_structured_error_not_a_hang`
(asserts `execute()` returns well before the mocked 1.5s call would have).

## 9. Observability

Every execution logs a structured `TOOL_CALLED` (start, and again on
completion with `status`/`latency_ms`/`result_count`) or `TOOL_FAILED`
event via `app.logging.log_event`, which already redacts
credential-shaped keys/values before anything reaches a handler
(`app/logging.py::RedactionFilter`/`scrub`) — reused unchanged, not
duplicated.

## 10. MCP boundary

The project's TigerGraph MCP server (`tigergraph-mcp` 1.0.3, 37 read-only
tools, `app/mcp/config.py`) is **not** the mechanism these six tools use
— they call `app/tigergraph/queries.py` directly, the same as Phase 2A/2B
always have. The application-level `ToolRegistry` remains the only
boundary a future agent should see, regardless of what runs underneath
any given tool. `app/mcp/config.py::ALWAYS_BLOCKED` (`drop_graph`,
`clear_graph_data`, `drop_all_data_sources`) is confirmed disjoint from
the registered tool names
(`TestSecurityBoundary::test_mcp_destructive_blocklist_is_disjoint_from_the_registry`).

## 11. Security restrictions

- No `execute_gsql` / `run_query` / `run_raw_tigergraph` tool is defined
  anywhere in `app/investigation/`.
- `ALLOWED_INVESTIGATION_TOOLS` is the only source `build_default_registry()`
  reads from — a fixed tuple, not a scan of `tools.py`'s namespace.
- `registry.get()` / `registry.execute()` raise `ToolNotRegisteredError`
  for any name outside that tuple — verified against both a mocked
  registry and the live client
  (`test_no_raw_gsql_tool_reachable_even_against_the_live_client`).
- Every registered tool's metadata reports `read_only: True`.

## 12. Latency — real measurements, not estimates

Measured via `registry.execute()` against the live `HHGOA_FRAUD` graph,
fixture transaction `2987937`, 3 samples per tool after one warm-up call:

| Tool | Samples (ms) | Avg (ms) |
|---|---|---|
| `get_transaction_context` | 967, 892, 924 | 928 |
| `find_shared_card_activity` | 847, 853, 802 | 834 |
| `find_shared_device_activity` | 800, 837, 835 | 824 |
| `find_shared_address_activity` | 1601, 1663, 1716 | 1660 |
| `find_shared_email_activity` | 858, 878, 1129 | 955 |
| `investigate_transaction_network` | 891, 840, 884 | 872 |

All within Phase 2A's documented 0.3–2.6s interpreted-query range
(docs/phase-2-graph-analysis.md §"Interpreted-query latency"). Notably
slower: `find_shared_address_activity`, consistent with the already-known
Address fan-out finding (312 related transactions here, an order of
magnitude more than Card's 12) — the tool layer surfaces more rows, not
a different query plan. No premature optimization / query-installation
work was done this phase; if this latency proves unacceptable for a
LangGraph hot path, that is a Phase 2 follow-up
(`scripts/install_queries.py`, not built yet — see
docs/phase-2-graph-analysis.md's own note on this).

`ToolMetadata.estimated_cost` is a static `"MEDIUM"` for all six tools —
a coarse, honestly-labeled PROJECT-DERIVED tier (interpreted queries,
sub-3s), not an invented numeric cost.

## 13. Determinism

Given the same TigerGraph state (or the same mocked Phase 2A result),
`registry.execute()` produces an identical `InvestigationToolResult`:
same `status`, same `evidence.evidence_id`, same `evidence.metrics`, same
`summary`. Verified offline
(`TestDeterminism::test_identical_mocked_response_produces_an_identical_result`)
and live
(`test_result_is_deterministic_across_two_live_calls`). No randomness, no
LLM, no wall-clock-dependent content in any field except `latency_ms`
itself.

## 14. Risk / evidence separation (carried forward from Phase 2B)

This phase does not touch `dataset_risk_score` or `Evidence.quality` at
all — `InvestigationToolResult.evidence` is the exact Phase 2B `Evidence`
object. The binary `isFraud` dataset label continues to surface only as
`get_transaction_context`'s `is_fraud_label` metric, never as a
probability, confidence, or "agent risk" value. See
docs/phase-2-evidence-model.md for the full rationale; unchanged here.

## 15. Not built this phase (by design)

LangGraph, an LLM, an uncertainty engine, next-best-action, a policy
engine, case memory, GraphRAG, and the frontend are all explicitly out of
scope for Phase 2C. `ToolRegistry` and `InvestigationToolResult` are the
intended surface the next phase (2D — deterministic investigation
service) builds on.
