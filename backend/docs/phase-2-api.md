# Phase 2K — Backend API / Serving Layer

Status date: 2026-09-22. Every command and result below was actually run
in this session (offline `TestClient`, a real `uvicorn` process, and the
live TigerGraph instance where noted) - nothing is inferred.

## 1. Purpose

A thin HTTP serving boundary over the already-complete deterministic
fraud-investigation backend and LangGraph agent (Phases 1-2J, unmodified
by this phase except one additive field - see §11). It exists so a
future frontend (Phase 2L) can start and retrieve investigations without
touching TigerGraph, an LLM SDK, or any internal Python object directly.

```
Frontend -> HTTP API -> Agent/LangGraph -> deterministic backend -> TigerGraph/CaseMemory
```

`app/api/` contains **no** investigation, uncertainty, policy, or
case-management logic. Every route handler does exactly one thing: call
an existing application-layer method (`AgentOrchestrator.run`,
`CaseManager.get_case`, `CaseMemory.retrieve_similar`,
`InvestigationRegistry.get`) and reshape its already-typed output into
an API response model.

## 2. Architecture

```
app/api/
    __init__.py       - exports create_app
    app.py             - create_app() factory + exception handlers
    models.py          - typed request/response models
    errors.py          - ErrorCode enum + APIError + subclasses
    registry.py        - InvestigationRegistry (thin in-memory cache - see §11)
    dependencies.py    - FastAPI Depends providers
    routes/
        __init__.py    - api_router aggregation
        health.py       - GET /health, GET /health/dependencies
        investigations.py - POST/GET /investigations
        cases.py        - GET /cases/... (record, evidence, history, similar, context)
```

**Framework choice**: `fastapi`/`uvicorn` were already declared in
`pyproject.toml`'s dependencies (never previously used by any code) -
this phase is the first thing to actually import them. No second web
framework was introduced; `app/api/` is the only HTTP-facing code in the
repository.

**No new database.** `CaseStore` (Phase 2G's `InMemoryCaseStore`,
unmodified) is reused as-is - see §11 for the one explicit,
process-local limitation this implies.

## 3. Startup

```bash
cd backend
uvicorn app.api.app:create_app --factory --reload
```

Verified in this session (non-`--reload`, to get a clean log):

```
$ uvicorn app.api.app:create_app --factory --port 8123
INFO:     Started server process [...]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8123
$ curl http://127.0.0.1:8123/health
{"status":"ok","service":"hhgoa-fraud-agent"}
```

`create_app()` does no I/O: it builds the FastAPI app, includes routes,
and registers exception handlers only. `TigerGraphClient` is lazy by
construction (Phase 1); the LLM client and the `CaseStore` are created
on first request, not at import or startup time (see §7).

## 4. Configuration

Reuses `app.config.Settings` (Phase 1, unmodified) - the same `.env` the
rest of the backend reads. No API-specific environment variable was
added. Relevant existing fields: `API_HOST`, `API_PORT` (not enforced by
`create_app()` itself - pass `--host`/`--port` to `uvicorn`, or read them
yourself when invoking it), `TG_*`, `OPENAI_API_KEY`/`OPENAI_MODEL`,
`LOG_LEVEL`.

## 5. Endpoints

| Method & path | Purpose |
| --- | --- |
| `GET /health` | Application liveness only - no dependency call |
| `GET /health/dependencies` | Real TigerGraph probe + LLM configuration check |
| `POST /investigations` | Start an investigation via `AgentOrchestrator` |
| `GET /investigations/{investigation_id}` | Retrieve a prior investigation result (this process only - §11) |
| `GET /cases/{case_id}` | The `CaseRecord`, typed, as-is |
| `GET /cases/{case_id}/evidence` | Per-item `Evidence` (full detail when available - §11) |
| `GET /cases/{case_id}/history` | Timeline derived from `CaseRecord` fields |
| `GET /cases/{case_id}/similar` | `CaseMemory.retrieve_similar()`, unmodified |
| `GET /cases/{case_id}/context` | The cached `InvestigationContext` (GraphRAG bundle) - §11 |
| `GET /docs`, `GET /openapi.json` | FastAPI's own interactive docs / schema |

### 5.1 `GET /health`

```bash
curl http://localhost:8000/health
```
```json
{"status": "ok", "service": "hhgoa-fraud-agent"}
```

### 5.2 `GET /health/dependencies`

Actually calls `TigerGraphClient.health()` (a real probe, Phase 1,
unmodified - never fabricates "healthy"). The LLM entry reports whether
`OPENAI_API_KEY`/`OPENAI_MODEL` are *configured* - connectivity to the
LLM provider is never probed here (a real call costs money/time); the
`detail` field says so explicitly so the two meanings are never
conflated.

```json
{
  "status": "ok",
  "service": "hhgoa-fraud-agent",
  "dependencies": [
    {"name": "tigergraph", "checked": true, "healthy": true, "detail": "reachable", "latency_ms": 1234.8},
    {"name": "llm", "checked": true, "healthy": false, "detail": "not configured - OPENAI_API_KEY/OPENAI_MODEL unset", "latency_ms": null}
  ]
}
```

### 5.3 `POST /investigations`

```bash
curl -X POST http://localhost:8000/investigations \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "2987937"}'
```

Request body (`InvestigationRequest`):

```json
{"transaction_id": "2987937", "trigger": "FRAUD_SIGNAL"}
```

- `transaction_id` - required, 1-64 characters.
- `trigger` - optional, one of `CaseTrigger`'s existing values
  (`FRAUD_SIGNAL`, `CUSTOMER_REPORT`, `ANALYST_REQUEST`,
  `AGENT_REQUEST`, `UNKNOWN`; default `UNKNOWN`). No new trigger value
  was invented for this phase.

Response (`InvestigationResponse`, HTTP 200 on `COMPLETED` or
`LIMIT_REACHED`; see §6 for `FAILED`):

```json
{
  "investigation_id": "case-4e1e95885469438598800021bd03c2bd",
  "case_id": "case-4e1e95885469438598800021bd03c2bd",
  "transaction_id": "2987937",
  "status": "COMPLETED",
  "completed": true,
  "iterations": 1,
  "tool_calls": 1,
  "findings": ["..."],
  "evidence": [{"evidence_id": "...", "evidence_type": "...", "status": "SUCCESS", "observation": "...", "interpretation": "...", "quality": "...", "provenance": {"source": "tigergraph", "source_query": "get_transaction_context", "transaction_id": "2987937"}}],
  "evidence_summary": {"evidence_counts": {"...": 1}, "query_status": {"...": "SUCCESS"}},
  "uncertainty": {"uncertainty_level": "LOW", "evidence_coverage": 1.0, "...": "..."},
  "policy_decision": {"action": "CREATE_CASE", "approval_required": true, "approval_route": "ANALYST", "executable": false, "...": "..."},
  "requested_evidence": [],
  "historical_context": [],
  "context": {"context_format_version": "v1", "current_evidence": ["..."], "historical_cases": ["..."], "recurring_patterns": ["..."], "missing_information": ["..."]},
  "explanation": "...",
  "next_step": "Awaiting ANALYST approval for the recommended action (CREATE_CASE).",
  "error": null
}
```

No field carries the LLM's raw reasoning trace or hidden chain-of-thought
- `explanation` is `LLMClient.generate_explanation()`'s output (Phase
2H: prose grounded in the same structured facts, never free reasoning),
and `agent_messages` (the orchestrator's internal `AgentEventType` audit
trail) is deliberately **not** included in this response - it is
observability data, not part of the API's external contract.

### 5.4 `GET /investigations/{investigation_id}`

Retrieves a prior `POST /investigations` result. See §11 for the exact
scope of what "prior" means.

### 5.5 `GET /cases/{case_id}`

Returns `app.case.models.CaseRecord` (Phase 2G, unmodified) directly,
serialized. `404 NOT_FOUND` if the case does not exist.

### 5.6 `GET /cases/{case_id}/evidence`

```json
{
  "case_id": "case-...",
  "transaction_id": "2987937",
  "detail_available": true,
  "evidence": [{"evidence_id": "...", "evidence_type": "...", "observation": "...", "interpretation": "...", "quality": "...", "status": "...", "provenance": {"...": "..."}}],
  "evidence_ids": ["..."],
  "evidence_types": ["..."]
}
```

`detail_available=false` (with `evidence: []` and only the compact
`evidence_ids`/`evidence_types`) when the case was not investigated by
this API process - see §11.

### 5.7 `GET /cases/{case_id}/history`

```json
{
  "case_id": "case-...",
  "events": [
    {"event_type": "CASE_CREATED", "occurred_at": "...", "description": "...", "ref_id": "case-..."},
    {"event_type": "RECOMMENDATION_CREATED", "occurred_at": "...", "description": "...", "ref_id": "..."}
  ],
  "note": "Derived only from timestamped CaseRecord fields..."
}
```

Event types actually derivable from `CaseRecord`: `CASE_CREATED`,
`FINDING_ADDED`, `RECOMMENDATION_CREATED`, `APPROVAL_RECORDED`,
`ACTION_RECORDED`, `OUTCOME_RECORDED`. `EVIDENCE_ATTACHED` and
`STATUS_CHANGED` are **not** produced - `CaseRecord` keeps only the
current `evidence_ids`/`status`, not a change log for either, and this
endpoint does not fabricate timestamps it does not have.

### 5.8 `GET /cases/{case_id}/similar`

Query params: `limit` (default 5, 1-50), `min_similarity` (default 0.0,
0.0-1.0) - passed straight through to
`CaseMemory.retrieve_similar(case, limit=..., min_similarity=...)`, no
second similarity implementation. Any `CaseOutcome.is_synthetic=true`
case in the result is exactly as synthetic in the response - the `note`
field says so explicitly, matching the rest of this project's
"SYNTHETIC DEVELOPMENT CASE" labeling convention.

### 5.9 `GET /cases/{case_id}/context`

Returns the cached `InvestigationContext` (`available: true`) when this
process produced it, or `available: false` with an explanatory `note`
otherwise - never silently rebuilt from TigerGraph (see §11).

## 6. Error model

Every error response:

```json
{"error": {"code": "NOT_FOUND", "message": "Case 'x' was not found.", "details": {"case_id": "x"}}}
```

| Code | HTTP status | When |
| --- | --- | --- |
| `INVALID_REQUEST` | 400 | Request body/query fails validation (e.g. empty `transaction_id`, unknown `trigger`) |
| `NOT_FOUND` | 404 | Unknown `case_id` / `investigation_id`, or an unmapped path |
| `INVESTIGATION_FAILED` | 502 | The deterministic backend raised (`AgentStatus.FAILED`, not caused by the LLM step) |
| `AGENT_FAILED` | 502 | The LLM call itself failed or returned unparseable output |
| `INVESTIGATION_LIMIT_REACHED` | - | **Not** an error response - see below |
| `DEPENDENCY_UNAVAILABLE` | 503 | The LLM is not configured for this deployment (TigerGraph failures surface via `INVESTIGATION_FAILED` instead, since `AgentOrchestrator` already classifies them that way) |
| `INTERNAL_ERROR` | 500 | Any other unhandled exception - message is always the fixed sentence `"An internal error occurred."`; the real exception is logged server-side only |

**`INVESTIGATION_LIMIT_REACHED` is deliberately never a thrown error.**
When `AgentOrchestrator` hits `max_iterations`, it still produces a
complete, valid `AgentInvestigationResult` - `PolicyEngine`'s own
deterministic conservatism (Phase 2F) already handled the "not enough
evidence" case correctly. Representing that as an HTTP error would
misrepresent a working safety mechanism as a failure. `POST
/investigations` instead returns `200` with
`"status": "LIMIT_REACHED"`, `"completed": false`, and a real (not
fabricated) `policy_decision` - this is what "use the existing agent
status" means in the phase brief. Verified in
`tests/api/test_investigations.py::TestIterationLimit`.

Every response the framework itself generates - FastAPI's own request
validation (`RequestValidationError`) and Starlette's routing errors
(unmatched path -> its own `404`) - is normalized into the same
`{"error": {...}}` envelope by `app/api/app.py`'s exception handlers, so
a client never has to branch on two different error shapes.

Nothing here ever returns a stack trace, an exception's raw `str()`, an
API key, a TigerGraph credential, raw GSQL, or an MCP internal - verified
in `tests/api/test_security.py` (§10).

## 7. Sync/async boundary

`AgentOrchestrator.run()` is synchronous end to end - it calls a
synchronous `TigerGraphClient` (`pyTigerGraph`, blocking HTTP under the
hood) and a synchronous `LLMClient` (`langchain_openai`, also blocking).
Every route handler in this phase is declared `def`, not `async def`,
**on purpose**: FastAPI/Starlette runs a synchronous path-operation
function in an external threadpool automatically. This is the correct,
minimal boundary here - wrapping an already-blocking, multi-second call
in `async def` with an `await` on a sync function would not make it
non-blocking, it would only make it *look* async while still blocking
the event loop (the explicit instruction this phase gives against "fake
async for appearance"). No request gets an artificially short timeout;
`uvicorn`'s own default request handling has no hard timeout for this
kind of long-running, CPU/IO-bound-in-a-thread request.

## 8. Dependency injection (testability)

Every external or shared object is behind a `Depends`-injected function
in `app/api/dependencies.py`:
`get_tigergraph_client`/`get_llm_client`/`get_case_store`/
`get_case_manager`/`get_case_memory`/`get_investigation_service`/
`get_orchestrator`/`get_investigation_registry`. Tests override exactly
the ones they need via `app.dependency_overrides[...]` - e.g.
`tests/api/conftest.py` overrides `get_tigergraph_client` (a fake,
duck-typed client + monkeypatched `app.investigation.tools.queries`,
same convention `tests/unit/test_agent_orchestrator.py` established) and
`get_llm_client` (`FakeLLMClient`), so the full real
`AgentOrchestrator` → `InvestigationService` → `UncertaintyEngine` →
`PolicyEngine` → `CaseManager` chain runs for real in every offline API
test, with **no live TigerGraph connection and no real OpenAI call**.

`get_case_store`/`get_investigation_registry` are cached on
`request.app.state` - one shared `InMemoryCaseStore` /
`InvestigationRegistry` per app instance/process, so a case created by
`POST /investigations` is immediately visible to `GET /cases/{case_id}`
within the same run.

## 9. Tests

- `tests/api/conftest.py` - shared fixtures (fake graph, fake/overridden
  dependencies)
- `tests/api/test_health.py` - spec item A
- `tests/api/test_investigations.py` - spec items B, C, J, K, plus
  investigation retrieval
- `tests/api/test_cases.py` - spec items D, E, F, G, H, I
- `tests/api/test_security.py` - spec item L
- `tests/tigergraph/test_api_live.py` - §25's live verification (real
  TigerGraph, `FakeLLMClient`)

Test-to-spec-item mapping, exact:

| Item | Test |
| --- | --- |
| A. Health | `test_health.py::TestHealth::test_health_is_200` |
| B. Valid investigation | `test_investigations.py::TestValidInvestigation::test_request_flows_through_the_api_to_the_agent_and_back` |
| C. Invalid transaction | `test_investigations.py::TestInvalidRequest` |
| D. Unknown case | `test_cases.py::TestUnknownCase` |
| E. Case retrieval | `test_cases.py::TestCaseRetrieval` |
| F. Evidence endpoint | `test_cases.py::TestCaseEvidence` |
| G. History endpoint | `test_cases.py::TestCaseHistory` |
| H. Similar cases | `test_cases.py::TestSimilarCases` |
| I. Context endpoint | `test_cases.py::TestCaseContext` |
| J. Agent failure | `test_investigations.py::TestAgentFailure` |
| K. Iteration limit | `test_investigations.py::TestIterationLimit` |
| L. Security | `test_security.py` |

## 10. Security boundaries

- **No arbitrary graph access.** No route accepts a GSQL string, a raw
  query name, or MCP parameters - verified structurally in
  `test_security.py::TestNoArbitraryGraphAccess` by walking the actual
  generated OpenAPI schema. The frontend never talks to TigerGraph
  directly; every graph call happens inside `InvestigationService`,
  reached only via `AgentOrchestrator`.
- **No secret/credential leakage.** `test_security.py::TestNoSecretLeakage`
  asserts real configured secret values (`TG_SECRET`, `TG_PASSWORD`,
  `TG_API_TOKEN`, `TG_JWT_TOKEN`, `OPENAI_API_KEY`) never appear in any
  response body, across an investigation result, a case record, and the
  dependency health check. `Settings`' `SecretStr` fields (Phase 1,
  unmodified) already never render via `str()`/`repr()`.
- **No arbitrary code execution.** No route evaluates a request body as
  code, and no `eval`/`exec`/shell call exists anywhere in `app/api/`.
- **Internal errors are sanitized.** Any unhandled exception is logged
  server-side with `exc_info` and returns only
  `{"error": {"code": "INTERNAL_ERROR", "message": "An internal error occurred."}}`
  to the client - verified in
  `test_security.py::test_unexpected_internal_error_never_leaks_its_message`.
- **Input validation.** `transaction_id` is length-bounded (1-64 chars);
  `trigger` is constrained to the existing `CaseTrigger` enum; FastAPI
  itself rejects any malformed JSON body before a route ever runs.

## 11. Known limitations (stated plainly, not hidden)

1. **`InMemoryCaseStore`, no new database.** Cases do not survive an API
   process restart - unchanged from Phase 2G, and explicitly not
   "fixed" here per the instruction not to introduce a new database for
   this phase.
2. **No separate investigation-record persistence.**
   `AgentInvestigationResult` is not a persisted domain model (Phase 2G
   deliberately keeps `CaseRecord` compact - see its module docstring).
   `investigation_id` is therefore `case_id` when a case was created, or
   an API-generated id when the investigation failed before one existed.
   `GET /investigations/{id}` only ever finds what this API process
   itself ran (via `app/api/registry.py`'s `InvestigationRegistry`, a
   plain in-memory dict, cleared on restart) - it does not reconstruct
   history from `CaseStore`.
3. **Full evidence/context detail is process-local.**
   `GET /cases/{case_id}/evidence` and `.../context` return full detail
   (`detail_available`/`available: true`) only for cases this process
   investigated - because `CaseRecord` stores compact `evidence_ids`
   references, not the full `Evidence` list, by design (Phase 2G). A
   case retrieved from a `CaseStore` this process did not itself
   populate degrades gracefully to the fields `CaseRecord` does carry,
   with an explanatory `note` - never fabricated.
4. **One additive backend change.** `AgentInvestigationResult` gained a
   new `evidence: list[Evidence]` field (`app/agent/state.py`,
   `app/agent/orchestrator.py`), populated from the same
   `snapshot.evidence_bundle.evidence` the orchestrator already reads to
   build `evidence_summary` - no new computation, no changed decision
   logic, additive-only (default `[]`), and the full offline (302→302,
   unaffected) and live (76→76, unaffected) Phase 2J regression suites
   were re-run clean after this change (see §12). Without it, `GET
   /cases/{case_id}/evidence` could not return per-item
   observation/interpretation/provenance/quality/status at all.
5. **No API-level rate limiting, auth, or pagination** - out of scope
   for this phase per its own instructions (no authentication system, no
   new infrastructure).

## 12. Regression

Phase 2K-specific:

```
tests/api          : 27/27 passed
tests/tigergraph/test_api_live.py : 3/3 passed (live)
```

Full offline (`tests/unit` + `tests/api`):

```
329 passed
```

Full live (`-m tigergraph`, includes Phase 2J's benchmark-live tests and
this phase's `test_api_live.py`):

```
79 passed
```

`ruff check` on every Phase 2K file (`app/api/`, `tests/api/`, the two
modified agent files): clean. (Pre-existing lint debt in
`tests/unit/test_tigergraph_diagnostics.py`, untouched by this phase, is
unrelated - confirmed by scoping `ruff check` to exactly the files this
phase created/modified.)

## 13. Performance (measured, not optimized - per instruction)

| Call | Latency | Note |
| --- | --- | --- |
| `GET /health` | ~70 ms (`TestClient`, includes first-request app warm-up) | No dependency call |
| `GET /health/dependencies` (live) | ~1.2 s | Dominated by the real TigerGraph `echo()` round-trip |
| `POST /investigations` (live, transaction `2987937`) | ~6.9 s | Dominated by TigerGraph query latency - the same 5.9-8.0s per case already measured in Phase 2J (`docs/phase-2-benchmark-report.md` §3a/§5); the API adds negligible overhead on top |
| `GET /cases/{case_id}` (live) | ~22 ms | In-memory `CaseStore` lookup only |

Dominant cost end to end is live TigerGraph query latency inside
`InvestigationService` (unchanged from Phase 2J's finding), not the API
layer. Per instruction, this phase does not attempt to optimize it.

## 14. Development/test mode

No separate "test mode" flag exists. Offline tests achieve determinism
purely through dependency overrides (§8) - the same `create_app()` runs
in tests and in production; nothing branches on `app_env` inside
`app/api/`.
