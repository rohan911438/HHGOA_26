# Phase 2D — Deterministic Investigation Service

Status: **COMPLETE**. Offline: 82/82 PASS (64 Phase 2C + 18 Phase 2D).
Live: 13/13 PASS (9 Phase 2C + 4 Phase 2D). Full suite: 171/171 PASS.

**LangGraph does NOT exist yet.** This phase is deterministic
infrastructure only — no LLM, no agent reasoning, no frontend. It is the
foundation a future LangGraph agent will orchestrate, built so that
foundation is fully testable and predictable without one.

## 1. What this phase adds

A service that runs the fixed six-tool investigation plan through the
Phase 2C `ToolRegistry` and assembles the results into one
`InvestigationSnapshot` — deterministically, with no fraud verdict.

```
InvestigationService.investigate_transaction(transaction_id, client)
        |
        v
get_transaction_context   (always first, blocking)
        |
        v
ToolRegistry.execute() for the remaining five tools
(sequential by default; bounded-concurrency opt-in)
        |
        v
Evidence items (Phase 2B normalizers, unchanged)
        |
        v
app.evidence.aggregate.deduplicate_evidence + build_summary -> EvidenceBundle
        |
        v
InvestigationSnapshot
```

`InvestigationService` imports only `app.investigation.registry` for
TigerGraph access — never `app.tigergraph.queries` or
`app.tigergraph.client` directly, matching this phase's required
architecture (`InvestigationService -> ToolRegistry -> InvestigationTool
-> Evidence -> TigerGraph`, never `InvestigationService -> queries.py`).

## 2. Files

```
app/investigation/
    snapshot.py    InvestigationSnapshot, InvestigationStatus, CoverageSummary, ToolExecution, INVESTIGATION_PLAN
    service.py     InvestigationService

app/evidence/aggregate.py   (extended, not duplicated - see §7)
```

## 3. InvestigationSnapshot

```python
class InvestigationSnapshot(BaseModel):
    investigation_id: str
    transaction_id: str
    started_at: datetime
    completed_at: datetime
    transaction_context: Evidence | None       # pointer into evidence_bundle, not a duplicate
    evidence_bundle: EvidenceBundle | None      # Phase 2B model, unchanged
    tools_executed: list[str]                   # in fixed INVESTIGATION_PLAN order
    tool_results: dict[str, InvestigationToolResult]  # Phase 2C model, unchanged
    execution_log: list[ToolExecution]           # lightweight per-tool audit trail
    coverage: CoverageSummary
    warnings: list[str]
    status: InvestigationStatus                  # COMPLETED / PARTIAL / FAILED
```

Every field is a Pydantic model, `str`/`float`/`int`, `datetime`, or a
list/dict of those. No `TigerGraphConnection`, `Thread`, `Executor`, or
raw exception ever appears — `snapshot.model_dump(mode="json")` is
directly JSON-serializable, verified live
(`test_snapshot_round_trips_through_json`) and offline
(`test_snapshot_is_json_serializable`).

## 4. No fraud verdict

`InvestigationSnapshot` has no `fraud_verdict` or `final_fraud_score`
field — enforced by
`TestNoFraudVerdict::test_no_verdict_field_exists_anywhere_on_the_snapshot_model`,
which asserts those names are absent from `InvestigationSnapshot.model_fields`,
and re-verified against a real snapshot live
(`test_no_fraud_verdict_anywhere_in_a_real_snapshot`). The dataset's
binary `isFraud` label continues to surface only as
`EvidenceBundle.dataset_risk_score` (Phase 2B, unchanged) — a dataset
fact, never an investigation conclusion, never called "confidence."

## 5. Investigation plan and execution order

```python
INVESTIGATION_PLAN = (
    "get_transaction_context",
    "find_shared_card_activity",
    "find_shared_device_activity",
    "find_shared_address_activity",
    "find_shared_email_activity",
    "investigate_transaction_network",
)
```

`get_transaction_context` always runs first and blocks — it establishes
that the transaction exists. If it errors (including `NOT_FOUND`), the
investigation stops immediately with `status=FAILED` and the remaining
five tools never run (`TestContextFailureStopsTheInvestigation`) —
avoiding pointless TigerGraph load against a transaction that may not
exist. No other reordering/skipping optimization is applied: the default
behavior runs the complete plan, per the explicit "remain complete and
predictable" requirement.

## 6. Status semantics — exact logic

```
FAILED     get_transaction_context itself ended ERROR (handled by an
           early return; the remaining five tools never execute)
PARTIAL    context succeeded, but not every planned tool ran (overall
           timeout cut it short) OR at least one tool that did run
           ended ERROR
COMPLETED  context succeeded and every planned tool completed as
           SUCCESS or EMPTY
```

`EMPTY` is never conflated with `ERROR` or with "no fraud": a tool that
ran successfully and found nothing keeps the investigation `COMPLETED`
(`TestSuccessfulInvestigation::test_empty_results_still_complete_not_partial`).
An `ERROR`'d tool's `Evidence` item keeps `status=ERROR` inside
`evidence_bundle` too — never silently downgraded to `EMPTY`
(`test_error_status_evidence_is_preserved_as_error_not_dropped`) — so a
future uncertainty layer can tell "no relationship found" apart from
"we don't know."

## 7. Evidence aggregation — reused, not duplicated

Phase 2B's `aggregate_evidence()` re-queries TigerGraph itself; calling
it from the service would run every query twice. Instead,
`InvestigationService` collects the `Evidence` items the registry
already produced (`InvestigationToolResult.evidence`) and reuses Phase
2B's own building blocks directly:

- `app.evidence.aggregate.deduplicate_evidence(evidence_items)` (already public, unchanged)
- `app.evidence.aggregate.build_summary(...)` — **made public this phase**
  (was `_build_summary`; purely a visibility change, zero behavior
  change, `aggregate_evidence()` itself still calls it internally and
  its own 31 tests still pass unchanged)
- `app.evidence.aggregate.dataset_risk_score_from_is_fraud_label(...)` —
  **extracted this phase** from `_run_transaction_context`'s inline
  `1.0/0.0` mapping, so the mapping exists in exactly one place shared by
  both `aggregate_evidence` (Phase 2B) and `InvestigationService` (Phase
  2D, operating on the already-normalized `is_fraud_label` metric rather
  than re-fetching `TransactionContext.attributes`)

No evidence-shaping logic was recreated.

## 8. Concurrency

`investigate_transaction(..., concurrent=False)` is the **default** —
sequential, one tool at a time, per this phase's emphasis on
conservatism and predictable default behavior. `concurrent=True` runs
the five non-context tools on a `ThreadPoolExecutor` bounded by
`max_concurrent_tools` (constructor parameter, default 3, configurable —
never all five threads at once, never unbounded).

Regardless of actual completion order, the snapshot's `tools_executed`,
`tool_results`, `coverage.per_tool_status`, and `execution_log` all
follow the fixed `INVESTIGATION_PLAN` order — verified with tools
mocked to complete in reverse order
(`TestDeterministicOrdering::test_output_order_is_fixed_regardless_of_completion_order`)
and live (`test_concurrent_investigation_produces_the_same_evidence_as_sequential`
asserts `execution_log` order too).

**Real defect found and fixed while building this**: `_execute_concurrent`
appends to `execution_log` in completion order (from iterating
`concurrent.futures.wait()`'s `done` set), not plan order — this was
initially left unsorted before being placed on the snapshot, so
`tools_executed` was correctly ordered but `execution_log` was not.
Fixed by re-sorting `execution_log` by `INVESTIGATION_PLAN` index right
before `_build_snapshot`. Caught by this phase's own ordering test
before being shipped.

### Why concurrency is opt-in, not the default

Live latency was measured for both modes against the real
`HHGOA_FRAUD` graph, fixture transaction `2987937`, 3 runs each:

| Mode | Samples (ms) | Avg (ms) |
|---|---|---|
| Sequential | 24745, 11710, 10752 | 15736 |
| Concurrent (max_concurrent_tools=3) | 3670, 7608, 2607 | 4628 |

**~3.4x average speedup** — a clear, real improvement, not a marginal
one. (Absolute numbers are noticeably higher and more variable than
Phase 2C's isolated per-tool measurements, 824–1660ms; this reflects
real Savanna-side load/variance across a session of many live test runs
today, not a regression in this phase's code — the *relative* speedup
between the two modes, measured back-to-back in the same session, is the
number that matters here.) Given this, `concurrent=True` is the
recommended mode for a latency-sensitive caller (e.g. a future FastAPI
endpoint, or the LangGraph hot path); `concurrent=False` remains the
default because it is the simplest, lowest-TigerGraph-load, and most
predictable behavior, and because the instructions this phase was built
against explicitly ask for a conservative default and configurability
rather than an aggressive one.

## 9. Timeouts compose: overall → per-tool

```
overall_timeout_seconds  (InvestigationService, optional; None = no cap)
        v
per-tool timeout_seconds  (ToolRegistry.execute, Phase 2C - always applies)
```

When an overall budget is set, each tool call's own timeout is
additionally capped to whatever budget remains, so one slow tool cannot
silently consume the whole investigation. A tool that does not get to
run, or does not finish, because the overall budget ran out is recorded
as skipped with an `INVESTIGATION_TIMEOUT` warning; the investigation
still returns a snapshot (status `PARTIAL`, never hangs) — verified with
a mocked 2-second call and a 0.3s overall budget, asserting wall time
stays under 1.5s
(`TestOverallTimeout::test_overall_timeout_marks_slow_tools_partial_without_hanging`).

Same underlying fix as Phase 2C's `ToolRegistry`: `_execute_concurrent`
calls `executor.shutdown(wait=False)` rather than using a `with`
block, so an overdue background call cannot block the caller past the
configured timeout.

## 10. No retries

A failed tool is reported as `ERROR` with its real `ToolErrorType` — this
phase adds no retry logic anywhere. TigerGraph can be genuinely,
temporarily unavailable (a Savanna workspace resuming, as observed
directly during Phase 2B/2C of this project); automatic retries would
multiply load during exactly the situation where that is least wanted.
If retries are ever added, they belong in a deliberate, bounded,
explicit resilience phase — not here.

## 11. Duplicate prevention

`InvestigationService` keeps a per-investigation cache
(`{tool_name: InvestigationToolResult}`, local to one
`investigate_transaction()` call, never shared across investigations).
The fixed plan never calls the same tool twice, so in practice this is a
guard against a future code path accidentally doing so, not something
the current plan triggers — verified directly by counting calls
(`TestDuplicatePrevention::test_each_tool_is_called_at_most_once_per_investigation`).
No cross-investigation caching, no Redis, no external dependency.

## 12. Investigation ID, evidence ID, transaction ID — kept distinct

`investigation_id` (`inv-<uuid4 hex>`) identifies one execution of
`investigate_transaction()`; it is never used as an `Evidence.evidence_id`
(which remains the deterministic `transaction_id:evidence_type:entity_id`
key from Phase 2B) and never equals `transaction_id`. Verified by
`test_investigation_id_is_distinct_from_evidence_and_transaction_id`.

## 13. Deterministic clock and ID injection

```python
InvestigationService(
    registry,
    id_generator=lambda: "fixed-id",   # default: f"inv-{uuid.uuid4().hex}"
    clock=lambda: some_datetime,       # default: datetime.now(UTC)
)
```

No test in this phase depends on `time.sleep()` for determinism or on
the real wall clock for reproducibility — two independently-constructed
services given the same injected clock/ID generator produce byte-identical
`investigation_id`/`started_at`/`completed_at`
(`test_deterministic_clock_and_id_injection_is_reproducible`). Tests that
exercise real timeout/ordering behavior use real `time.sleep()`
deliberately, to exercise real threading — a different, explicit
category of test, not the determinism guarantee above.

## 14. Coverage model

```json
{
  "per_tool_status": {
    "get_transaction_context": "SUCCESS",
    "find_shared_card_activity": "SUCCESS",
    "find_shared_device_activity": "EMPTY",
    "find_shared_address_activity": "SUCCESS",
    "find_shared_email_domain": "SUCCESS",
    "network_pattern": "SUCCESS"
  },
  "successful_tools": 4,
  "empty_tools": 1,
  "failed_tools": 0,
  "total_tools": 6
}
```

`total_tools` is the number of tools actually attempted in this run (6
unless the investigation was cut short by a context failure or an
overall timeout) — not a probability, not a score.

## 15. Failure taxonomy — preserved unchanged from Phase 2C

`VALIDATION_ERROR`, `NOT_FOUND`, `EMPTY_RESULT` (reserved, never
emitted), `QUERY_ERROR`, `AUTHENTICATION_ERROR`, `TIMEOUT`,
`TIGERGRAPH_UNAVAILABLE`, `INTERNAL_ERROR` — `ToolErrorType`
(`app/investigation/errors.py`) is reused unchanged. Every `ToolExecution`
audit record carries `error_type: ToolErrorType | None`, never a flattened
"Investigation failed" string as the only signal.

## 16. Observability

`CASE_STARTED` / `CASE_COMPLETED` structured log events
(`app.logging.InvestigationEvent`, reused unchanged) bracket each
investigation; the underlying `TOOL_CALLED`/`TOOL_FAILED` events from
`ToolRegistry.execute()` (Phase 2C) fire per tool as before. All
redaction (`app.logging.RedactionFilter`/`scrub`) is inherited unchanged
— no credential, token, or password is ever placed in a `ToolExecution`
record or a log payload.

## 17. Security

Secret scan (Step 28): clean. `.env` remains git-ignored and untracked;
only `.env.example` is tracked. No TigerGraph secret, API key, Kaggle
credential, or OpenAI credential found in any new or modified file for
this phase — only variable-name references, matching the pattern
established in Phase 2B/2C. `.gitignore` was not modified.

## 18. Known TLS issue — carried forward, unchanged

**KNOWN SECURITY ISSUE**: `pyTigerGraph` 2.0.4 unconditionally disables
certificate verification for any HTTPS host (root-caused in Phase 2B,
re-confirmed live during Phase 2B's resumed-workspace verification —
`InsecureRequestWarning` observed on every live call in this phase's own
test runs too). Not modified, not suppressed, and no unverified
workaround introduced this phase. The connection remains genuinely
TLS-encrypted but with the certificate chain unvalidated — a real MITM
exposure on an adversarial network, tracked separately as project
follow-up, not a Phase 2D concern.

## 19. Not built this phase (by design)

LangGraph, OpenAI/any LLM, agent reasoning, an uncertainty engine,
next-best-action, a policy engine, case memory, GraphRAG, and the
frontend are all explicitly out of scope. `InvestigationService` and
`InvestigationSnapshot` are the intended surface Phase 2E (Uncertainty
Engine) and, later, a LangGraph agent build on.
