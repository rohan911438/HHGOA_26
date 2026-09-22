# Phase 2M — Demo Script + Judging Alignment Checklist

Status date: 2026-09-22. This document is the presentation script and
internal capability checklist for submission — it does not add new
functionality and does not claim a score. See `../../README.md` for the
project overview and `phase-2-api.md`/`phase-2-frontend.md` for full
technical detail.

## Demo script (3:30–4:30 total)

Transaction used throughout: **`2987937`**. Start the backend
(`uvicorn app.api.app:create_app --factory --reload`) and frontend
(`npm run dev`) first — see `../../README.md`'s Setup section.

**0:00–0:30 — Problem + what the agent does**
Fraud investigation today means a human analyst manually pulling
transaction, card, device, address, and email linkage data, then judging
how confident that evidence is, then deciding what to do next. This
system automates the *investigation and reasoning* — gathering graph
evidence, assessing how uncertain the picture still is, retrieving
relevant history, and recommending a next step with a clear approval
requirement — while keeping a human in the loop for every action.

**0:30–1:00 — Enter transaction 2987937**
Type (or leave the default) `2987937` in the transaction field, click
**Investigate**. Narrate: this is a real API call to a live FastAPI
backend, which runs a LangGraph agent that calls a controlled,
six-tool investigation service against a real TigerGraph graph — nothing
in this UI is a canned response.

**1:00–2:00 — Investigation and evidence**
Once the result renders, walk the Evidence panel: shared card, shared
address, shared email domain, shared device (if linked), and the network
summary — each with its observation, interpretation, quality rating, and
provenance (which query produced it). **Explicitly show a LOW-quality
item if one is present** (this dataset's shared-address signal typically
is) — do not skip past it or make it look stronger than it is; that
restraint is itself part of what's being demonstrated. Point at the
graph relationship visualization built from that same evidence.

**2:00–2:45 — Uncertainty, history, findings**
Show the Uncertainty panel: level, the raw number, and its four
components (coverage/quality/conflict/completeness), plus the explicit
"uncertainty is not fraud probability" disclaimer. Show Historical
Context (similar cases, clearly labeled `SYNTHETIC DEVELOPMENT CASE`)
and Recurring Evidence Patterns. Show the Agent Findings panel's
grounded explanation — note aloud that this is a structured summary, not
a chain-of-thought dump; the UI has no such field to show even if asked.

**2:45–3:30 — Next-best-action, approval, case**
Show the Next-Best-Action panel: the recommended action, its rationale,
whether approval is required and by whom, and the explicit
"Recommendation only — no real-world action executed" line when
`executable=false`. Show the Case Timeline (case created → finding added
→ recommendation created → …), built from the real `GET
/cases/{id}/history` endpoint.

**3:30–4:30 — Why this is agentic, not a form**
Close by naming each piece explicitly: the agent used **controlled
tools** (never raw GSQL), **synthesized evidence** across multiple graph
signals, **evaluated uncertainty** deterministically, **retrieved
historical context**, **selected a next step** via a policy engine
separate from the LLM, and **created/updated a case** with full
traceability back to every evidence id it used. If TigerGraph happens to
be degraded during the live demo, that is itself worth showing: the
system still returns a complete, honest result (`REQUEST_MORE_EVIDENCE`,
not a fabricated verdict) — see `../../README.md`'s "What graceful
degradation looks like".

## Judging alignment checklist

This checks whether the required *capability* exists and is tested —
it does not assign a score or claim a ranking.

### Investigation Accuracy (25%) — gather and synthesize graph evidence

- [x] Six real, read-only GSQL-backed queries (`app/tigergraph/queries.py`), each independently live-tested (13/13, `docs/phase-2-graph-analysis.md`)
- [x] Evidence normalized with type, observation, interpretation, quality rating, and provenance (`app/evidence/`, 57/57 offline + 6/6 live)
- [x] Investigation service runs the full tool plan deterministically, sequential or bounded-concurrent (`app/investigation/service.py`, 18/18 + 4/4 live)
- [x] Network-level synthesis (`investigate_transaction_network`) cross-checked against the individual per-entity queries for consistency (`tests/tigergraph/test_investigation_queries.py::TestNetworkSummary`)

### Next-Best-Action (25%) — traceable action + approval route

- [x] Deterministic policy engine, separate from the LLM (`app/policy/`, 30/30 + 9/9 live)
- [x] Every `PolicyDecision` carries `action`, `rationale`, `approval_required`, `approval_route`, `executable` (always traceable to `evidence_ids`)
- [x] `executable` is `False` for every action this system can currently produce — recommendation, never execution
- [x] Exposed over a real API endpoint and rendered prominently in the UI (`NextBestActionPanel.tsx`)

### Case Summary (10%) — UI/API clearly summarizes the investigation

- [x] `GET /cases/{id}` returns a full typed `CaseRecord` (status, findings, decisions, actions, outcome)
- [x] `GET /cases/{id}/history` returns a real, non-fabricated timeline
- [x] Frontend renders case ID, transaction ID, investigation status, case status, uncertainty, and NBA together in one summary header

### Agentic Design (15%) — controlled tools, context, iteration, case state

- [x] LangGraph orchestrator with a real control-flow loop (`investigate → assess uncertainty → decide → gather more evidence or finish`), bounded by `max_iterations`/`max_tool_calls` (`app/agent/orchestrator.py`, 24/24 + 6/6 live)
- [x] Context/GraphRAG layer retrieves current facts, historical cases, and recurring patterns for the agent to reason over (`app/context/`)
- [x] Case state persists across the investigation (`CaseManager`/`CaseStore`/`CaseMemory`) and similar-case retrieval is exercised via its own API endpoint
- [x] The LLM's controlled surface is a strict enum-constrained schema — it cannot call tools directly or bypass any engine

### Innovation (15%) — graph + agent + uncertainty + case memory differentiation

- [x] Explicit, first-class uncertainty model distinct from a fraud score (coverage/quality/conflict/completeness, each independently computed)
- [x] GraphRAG-style structured retrieval (not a generic vector RAG) grounding every context item in a real evidence id or case id
- [x] Deterministic case-similarity/recurring-pattern detection reused identically by both the agent's context builder and the API's `/similar` endpoint (no second implementation)
- [x] A benchmark-discovery module that refuses to fabricate results when official data is absent, rather than faking a score

### Demo (10%) — clear, reproducible, complete workflow

- [x] One command per side to start (`uvicorn ... --reload`, `npm run dev`)
- [x] One transaction (`2987937`) exercises the full stack end to end
- [x] Real, non-hardcoded values render in the UI — verified by an integration test that never mocks `lib/api.ts` itself, only the network boundary
- [x] Degraded/error states are also demonstrable and honest, not hidden

## Explicitly not claimed

- No official HHGoa benchmark score (data unavailable — see `phase-2-benchmark-report.md`)
- No fraud-accuracy metric (this system does not produce a fraud verdict at all, by design)
- No ranking or self-assessed score against other submissions
