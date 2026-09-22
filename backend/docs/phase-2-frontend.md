# Phase 2L — Frontend / Fraud Investigation Dashboard

Status date: 2026-09-22. Every command and result below was actually run in
this session (Jest, `tsc`, `eslint`, `next build`, a real `uvicorn`
process, and the live TigerGraph instance where noted).

## 1. Frontend architecture

```
frontend/
  src/
    app/
      layout.tsx        - root layout (dark theme, metadata)
      page.tsx           - the single dashboard screen (client component)
      globals.css         - Tailwind v4 theme tokens (dark console palette)
    components/
      TransactionForm.tsx    LoadingState.tsx       ErrorBanner.tsx
      SummaryHeader.tsx       NextBestActionPanel.tsx
      EvidencePanel.tsx        GraphView.tsx
      UncertaintyPanel.tsx      AgentFindingsPanel.tsx
      HistoricalContextPanel.tsx RecurringPatternsPanel.tsx
      CaseTimeline.tsx           ui.tsx (shared primitives)
    lib/
      types.ts   - TypeScript types mirroring the backend's real OpenAPI schema
      api.ts     - the ONLY module that calls the backend (fetch wrapper)
    test/
      fixtures.ts - realistic fixtures shaped exactly like real API responses
  jest.config.mjs / jest.setup.ts
  .env.example
```

**Stack**: Next.js 16 (App Router, Turbopack), React 19, TypeScript,
Tailwind CSS v4. This was the stack already named as "planned" in
`frontend/README.md` before this phase (no frontend existed yet - see
§9 "First inspect", below). No other frontend framework, state-management
library, or chart/graph library was introduced - the whole dashboard is
plain React state (`useState`/`useCallback`) and one hand-built inline
SVG for the relationship graph (§5), per the instruction to keep this
minimal and avoid unnecessary dependencies.

**No backend code was modified in this phase**, except the two additive
Phase 2K changes disclosed and regression-tested in
`docs/phase-2-api.md` §11.4 (`AgentInvestigationResult.evidence`) - those
predate this phase and were not touched again here.

## 2. First inspect (what was found before building anything)

- `frontend/` contained only a placeholder `README.md` ("reserved, not
  started"), no `package.json`, no `src/`. It named Next.js as the
  planned stack.
- No `web/`/`ui/` directory, and no other `package.json` anywhere in the
  repo (backend is pure Python).
- `backend/docs/phase-2-api.md` and a live `/openapi.json` fetch from a
  running backend instance were used to build `src/lib/types.ts` field
  by field - every field name in that file was copied from the real
  schema, never guessed. See §8.

## 3. API integration

`src/lib/api.ts` is the only module in this codebase that calls
`fetch()`. Every component receives data as props; none calls the
network directly. The base URL comes from `NEXT_PUBLIC_API_URL`
(`.env.example` → copy to `.env.local`) - never hardcoded, and the app
throws a clear configuration error if it's unset rather than silently
defaulting to a guessed URL.

Non-2xx responses are parsed into the backend's own
`{"error": {"code", "message", "details"}}` envelope (see
`backend/docs/phase-2-api.md` §6) and thrown as a typed `ApiError`; a
`fetch()`-level failure (backend unreachable, DNS, CORS) is normalized
into the same `ApiError` shape with code `API_UNAVAILABLE` so every
consumer handles exactly one error type.

Endpoints actually called by the UI, and why:

| Endpoint | Called when | Why |
| --- | --- | --- |
| `POST /investigations` | user clicks Investigate | the primary action |
| `GET /cases/{id}` | after a case is created | `CaseStatus` (OPEN/PENDING_REVIEW/...) is **not** on `InvestigationResponse` - only on `CaseRecord` |
| `GET /cases/{id}/history` | after a case is created | the timeline; not present in the POST response at all |
| `GET /cases/{id}/similar` | after a case is created | exercises `CaseMemory.retrieve_similar()` fresh via its own endpoint, rather than only reusing the investigation-time snapshot |

`evidence` and `context` are **not** re-fetched from
`GET /cases/{id}/evidence` / `GET /cases/{id}/context` - the `POST
/investigations` response already includes full detail for a
same-process investigation (`detail_available`/`available: true`, per
`backend/docs/phase-2-api.md` §11.3), so a second call would be a pure
duplicate against data already in hand. This was a deliberate choice per
the instruction to avoid duplicate API calls; both endpoints exist and
work (see backend's own `tests/api/test_cases.py`), they're simply not
re-exercised by this minimal UI. `GET /investigations/{id}` is likewise
not wired into the UI - it is redundant with `GET /cases/{id}` for every
case this UI itself creates (`investigation_id == case_id` in the
success path - `backend/docs/phase-2-api.md` §11.2).

The three secondary calls (`getCase`/`getCaseHistory`/`getSimilarCases`)
run via `Promise.allSettled`, not `Promise.all` - one panel's fetch
failing never blanks an otherwise-successful investigation result (see
`page.tsx`).

## 4. Main screen and investigation flow

One screen (`src/app/page.tsx`). No routing, no navigation - matching
the instruction that this is a console, not a multi-page app.

```
Header ("HHGoa Fraud Investigation Agent")
  -> TransactionForm (default "2987937", NOT auto-submitted)
  -> [idle | loading | error | success]
       loading  -> LoadingState (generic "AI investigation in progress…")
       error    -> ErrorBanner
       success  -> SummaryHeader
                   NextBestActionPanel
                   EvidencePanel        | GraphView
                   UncertaintyPanel     | AgentFindingsPanel
                   HistoricalContextPanel | RecurringPatternsPanel
                   CaseTimeline
```

The transaction field defaults to `2987937` (the same transaction used
throughout Phase 2J/2K's live examples) but is fully editable, and
**investigation never starts automatically on page load** - the
instruction was explicit about this, and `page.test.tsx` asserts
`fetch` is never called before the button is clicked.

**Loading state is honest, not fabricated.** The backend's
`POST /investigations` is synchronous end to end (see
`backend/docs/phase-2-api.md` §7) - there is no server-sent stage event
to reflect. `LoadingState.tsx`'s rotating labels ("Mapping graph
relationships…", "Analyzing evidence…", …) are explicitly documented in
its own module comment as **cosmetic only** - they never claim the
backend is "currently" doing any one of them, only that a long real wait
(6-8s against live TigerGraph, per `docs/phase-2-benchmark-report.md`
§3a) isn't frozen.

## 5. Graph visualization

`GraphView.tsx` builds a radial SVG diagram **only from evidence fields
the API actually returns** - `Evidence.evidence_type`,
`Evidence.provenance.entity_id`, `Evidence.metrics.related_transaction_count`,
and `Evidence.related_entities` (a sample of related transaction ids;
see `backend/app/evidence/normalize.py`). No edge, count, or entity is
invented: an entity node only appears when the API returned that
evidence type, and the "N linked" label uses the API's own
`related_transaction_count` metric, not the (possibly-truncated) sample
array length.

This is intentionally a clean relationship diagram, not an
interactive/pannable graph library - per the instruction to prefer that
when the API's evidence-based data (not a raw graph query) is what's
available, rather than pulling in a graph-visualization dependency for
a hackathon-timeline dashboard. Current transaction, related entities,
and sample related transactions are visually distinguished (center
node, mid-ring nodes, small satellite dots) with a legend.

## 6. Evidence UI

`EvidencePanel.tsx` renders, per item: type, observation, interpretation,
quality, status, provenance (`source`/`source_query`), and the evidence
id - the exact field list the brief specified. **LOW-quality evidence is
rendered with the same restrained amber badge used for "requires
attention" states elsewhere (approval-required, missing-evidence) - never
upgraded to look stronger, and never colored red/green** (see §11
"Design"). `EvidencePanel.test.tsx` asserts a LOW-quality item's card
text contains "LOW" and does not contain "HIGH".

## 7. Uncertainty UI

`UncertaintyPanel.tsx` shows the uncertainty level, the raw
`overall_uncertainty` number, four progress meters (evidence coverage,
signal quality, signal conflict, data completeness), missing evidence
(or "Evidence Collection Complete" when there is none), conflicting
evidence, and the engine's factor list. A fixed disclaimer sentence -
**"Investigation uncertainty is not fraud probability."** - sits directly
under the number. No code path in this panel ever labels the number
"fraud probability/confidence" - `UncertaintyPanel.test.tsx` asserts
this directly.

## 8. Policy / next-best-action UI

`NextBestActionPanel.tsx` is the single most visually prominent panel
(a 2px accent border, the largest heading on the page) - it renders
`PolicyDecision.action`/`.rationale`/`.approval_required`/
`.approval_route`/`.executable`/`.requires_more_evidence` exactly as
returned, never a hardcoded value (`NextBestActionPanel.test.tsx`
renders both `CREATE_CASE` and a different `MONITOR_ACCOUNT` case to
prove this). When `executable=false` it shows
**"Recommendation only — no real-world action executed."**; when `true`,
that sentence is absent and no other component in this codebase ever
claims otherwise.

## 9. Historical case UI

`HistoricalContextPanel.tsx` renders each `SimilarCaseResult` from
`GET /cases/{id}/similar`: similarity score, matching features, relevant
findings, previous actions, and outcome. Any `CaseOutcome.is_synthetic
=== true` renders a `SYNTHETIC DEVELOPMENT CASE` badge - the exact label
requested, since the official HHGoa historical case dataset is
unavailable (`docs/phase-2-benchmark-report.md`). No case is ever shown
without checking this flag.

`RecurringPatternsPanel.tsx` renders `InvestigationContext.recurring_patterns`
(`ContextItem[]`, `context_type=DERIVED_PATTERN`). See §11 for why
occurrences/actions/outcomes appear embedded in the item's `content`
sentence rather than as separate columns - the API does not expose the
backend's internal `RecurringPattern` model as separate fields.

## 10. Error handling

| Scenario | Handling |
| --- | --- |
| Backend unreachable | `fetch()` throws → `ApiError(0, "API_UNAVAILABLE")` → friendly banner |
| Invalid transaction id | Backend's `400 INVALID_REQUEST` → banner with the backend's own message |
| Investigation/agent failure | Backend's `502 INVESTIGATION_FAILED`/`AGENT_FAILED` → banner |
| Case/investigation not found | Backend's `404 NOT_FOUND` → banner |
| Malformed/unexpected response | `response.json()` failing is caught; falls back to a generic `UNKNOWN_ERROR` banner, never a raw parse exception |
| A secondary panel's own fetch fails (case/history/similar) | That one panel shows its fallback empty/loading state; the rest of the already-successful result stays visible (`Promise.allSettled`, see §3) |

No stack trace, exception message, or internal detail is ever rendered
- `ErrorBanner.tsx` maps known error codes to a fixed, safe sentence and
falls back to the backend's own (already-sanitized, per
`backend/docs/phase-2-api.md` §10) `message` field otherwise.

## 11. Design

Dark, compact, "analyst console" aesthetic: near-black background,
subtle 1px borders, restrained blue accent for interactive/primary
elements, amber (never red) for "needs attention" states (LOW quality,
approval required, missing evidence), no gradients, no hero section, no
decorative animation beyond the loading indicator's pulse. Quality and
uncertainty badges deliberately never use red/green - see the module
comment on `QualityBadge` in `ui.tsx` - because this project's own
uncertainty/evidence-quality signals are explicitly not a
fraud-certainty score (`backend/app/uncertainty/models.py`'s module
docstring), and a red/green pill would visually imply exactly that.

## 12. Configuration

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

`.env.example` has this placeholder only - no real secret ever belongs
in a frontend `.env*` file, since the frontend never holds a TigerGraph
or LLM credential (see §14). `.gitignore` excludes `.env*` except
`.env.example` (matching the same convention `backend/.gitignore`
already uses).

## 13. Local startup

```bash
cd backend && uvicorn app.api.app:create_app --factory --reload   # terminal 1
cd frontend && npm install && cp .env.example .env.local && npm run dev   # terminal 2
```

Then open `http://localhost:3000`.

Verified this session: `npm run build` completed successfully
(Turbopack, 36.6s compile + typecheck), `npm run dev` served a real
`200` on `http://localhost:3000` with the correct page title text, and
a real backend on `http://127.0.0.1:8123` served `/health` at `200`
while the dev server was running.

## 14. Security

- **No secrets in the frontend.** `NEXT_PUBLIC_API_URL` is the only
  configuration value, and it is a URL, not a credential -
  `NEXT_PUBLIC_*` variables are inlined into the client bundle by
  design, which is exactly why nothing more sensitive than a URL is
  ever put behind that prefix here.
- **No TigerGraph/LLM credential anywhere in `frontend/`.** Grepped this
  session (`grep -ri "tg_secret\|api_key\|password" frontend/src`):
  zero matches outside the type name `ApiKey`-shaped strings that don't
  exist here at all.
- **No direct TigerGraph access.** `frontend/src/lib/api.ts` is the only
  network-calling module in the frontend and it only ever calls the
  backend's own documented endpoints (`/health`, `/investigations`,
  `/cases/...`) - never a TigerGraph host, never raw GSQL, never an MCP
  endpoint. There is no code path in this frontend capable of reaching
  TigerGraph directly even if credentials were somehow present.
- **`.env*` gitignored** except `.env.example` (§12).
- **No arbitrary code execution**: no `eval`, no `Function(...)`, no
  `dangerouslySetInnerHTML` anywhere in `src/`.

## 15. Testing

```
Test Suites: 9 passed, 9 total
Tests:       30 passed, 30 total
```

(Phase 2M: the integration test's original loading-state assertion raced
a fixed-delay mock and flaked intermittently under this machine's
variable load - split into a deterministic test using a manually-
controlled pending `Promise` (`page.test.tsx`'s "shows the loading state
while the request is genuinely still pending"), verified clean across 6
consecutive runs. No application code changed.)

- `TransactionForm.test.tsx` - default value, no auto-submit, editable
  input, submit payload, disabled-while-loading
- `LoadingState.test.tsx` - generic progress indicator
- `EvidencePanel.test.tsx` - full field rendering, LOW-quality never
  upgraded, no-detail-available fallback
- `UncertaintyPanel.test.tsx` - level/number rendering, the
  not-fraud-probability disclaimer, evidence-complete vs.
  evidence-required states
- `NextBestActionPanel.test.tsx` - renders the actual action (two
  different actions asserted, neither hardcoded), recommendation-vs-
  execution distinction
- `HistoricalContextPanel.test.tsx` - similarity/features/outcome
  rendering, the `SYNTHETIC DEVELOPMENT CASE` label
- `CaseTimeline.test.tsx` - real history events, empty-state fallback
- `ErrorBanner.test.tsx` - friendly messages, no stack-trace-shaped text
- `page.test.tsx` (**integration**) - the required
  transaction-input → API call → rendered-result flow, using a mocked
  `global.fetch` that speaks the *real* backend schema (via
  `src/test/fixtures.ts`, cross-checked against a live `/openapi.json`
  fetch - §8) through the *real*, unmocked `src/lib/api.ts`; also covers
  the no-auto-investigate case, a `400 INVALID_REQUEST` response, and an
  unreachable-backend (`fetch` rejection) case

`npx tsc --noEmit`: clean. `npm run lint` (ESLint via `eslint-config-next`):
clean, 0 warnings.

## 16. Known limitations

- **No graph library / no pan-zoom interactive graph.** `GraphView.tsx`
  is a static (per-render) radial SVG derived from evidence fields, not
  an interactive force-directed graph - a deliberate minimal choice (§5),
  not a missing feature the API would otherwise support.
- **Recurring-pattern occurrences/actions/outcomes are not separate
  columns.** The API exposes `RecurringPattern` only via
  `InvestigationContext.recurring_patterns` (`ContextItem[]`), whose
  `content` field already embeds the occurrence count, evidence types,
  related case ids, and historical actions/outcomes as one grounded
  sentence, and whose `relevance` field separately carries the numeric
  occurrence count. This panel renders exactly that; it does not
  regex-parse the sentence back into separate fields the API never
  exposed as such (see `RecurringPatternsPanel.tsx`'s module comment).
- **`GET /investigations/{id}` and `GET /cases/{id}/evidence`/`/context`
  are not called by this UI** (§3) - not because they're missing, but
  because the data they'd return is already in hand from
  `POST /investigations` for every case this UI itself creates.
- **No auth, no multi-user state, no persistence beyond the backend's
  own** `InMemoryCaseStore` (`backend/docs/phase-2-api.md` §11.1) -
  refreshing the page loses the currently-displayed result; the backend
  case itself is unaffected.
- **Browser-based live verification was not possible in this session**
  (the Claude-in-Chrome extension was not connected in this
  environment). Live end-to-end verification was instead done by
  running the real `src/lib/api.ts` module directly against a real,
  live-TigerGraph-backed backend instance (Node/`tsx`, not a
  reimplementation) - see the Phase 2L report's "Live Demo" section for
  the actual observed result, including a genuine TigerGraph Cloud
  token-minting outage encountered during that run and how the system
  handled it.
