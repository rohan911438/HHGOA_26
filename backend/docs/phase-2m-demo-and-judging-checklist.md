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
- [x] **Official benchmark:** all 20 HHGOA_IEEE cases investigated against the official graph (`HHGOA_IEEE`, 590,742 transactions). 8–9 counted GSQL tool calls per case: card history, customer cards, device neighbourhood, closed cases, similar cases, case memory (`docs/official-benchmark-report.md` §3)
- [x] Named, cited signals for every README pattern (card testing, CNP burst, new device, out-of-region with home activity, account-takeover markers) plus two undocumented schemes from the closed-case notes: a shared-device ring (fingerprint measured from CC-2649 et al.) and sub-$500 structuring (`app/benchmark/official/signals.py`)
- [x] HHG-014 (analyst request about "the same unusual device profile"): traced the device from the flagged transaction to **27 other customers' cards**, the same profile as the bank's closed ring cases
- [ ] Pattern / investigation accuracy: **not measurable**. The official package has no answer key; TigerGraph scores privately

### Next-Best-Action (25%) — traceable action + approval route

- [x] Deterministic policy engine, separate from the LLM (`app/policy/`, 30/30 + 9/9 live)
- [x] Every `PolicyDecision` carries `action`, `rationale`, `approval_required`, `approval_route`, `executable` (always traceable to `evidence_ids`)
- [x] `executable` is `False` for every action this system can currently produce — recommendation, never execution
- [x] Exposed over a real API endpoint and rendered prominently in the UI (`NextBestActionPanel.tsx`)
- [x] **Official benchmark:** NBA recorded **before and after** additional evidence for 20/20 cases, using the exact Fraud Policy v1.0 action identifiers; every route equals policy §2 for its action and exposure (checked in 20/20)
- [x] 12/20 cases requested evidence (R1/§3b: `customer_validation` ×9, `step_up_auth` ×3); the recommendation changed in 12/12; `what_changed` explains each
- [x] 8/20 stopped without asking, each with a `stop_reason` (§6 threshold, the customer's denial already in hand, or R8 escalation when the evidence cannot settle it)
- [x] Case vs. report decided by §3a: 5 SARs filed, 15 not, each with the rule cited
- [ ] NBA / approval accuracy: **not measurable** (no answer key)

### Case Summary (10%) — UI/API clearly summarizes the investigation

- [x] `GET /cases/{id}` returns a full typed `CaseRecord` (status, findings, decisions, actions, outcome)
- [x] `GET /cases/{id}/history` returns a real, non-fabricated timeline
- [x] Frontend renders case ID, transaction ID, investigation status, case status, uncertainty, and NBA together in one summary header
- [x] **Official benchmark:** 20 answer files in `cases/` with status, verdict, probability, pattern, affected transactions, exposure (§4), connected cards/devices, a typed evidence list (`graph`/`document`/`customer`/`external` + ref + entity IDs), similar prior cases and a short summary; all IDs verified to exist in the dataset
- [x] SAR narratives (5) built only from graph facts: who / what / when / where / how / why, 6–12 sentences, subjects checked
- [x] 20/20 answers pass 35–39 deterministic format and policy checks (`app/benchmark/official/validate.py`)

### Agentic Design (15%) — controlled tools, context, iteration, case state

- [x] LangGraph orchestrator with a real control-flow loop (`investigate → assess uncertainty → decide → gather more evidence or finish`), bounded by `max_iterations`/`max_tool_calls` (`app/agent/orchestrator.py`, 24/24 + 6/6 live)
- [x] Context/GraphRAG layer retrieves current facts, historical cases, and recurring patterns for the agent to reason over (`app/context/`)
- [x] Case state persists across the investigation (`CaseManager`/`CaseStore`/`CaseMemory`) and similar-case retrieval is exercised via its own API endpoint
- [x] The LLM's controlled surface is a strict enum-constrained schema — it cannot call tools directly or bypass any engine
- [x] **Official benchmark:** 12-step investigation per case, every step and tool call (with latency) recorded in `trace.json`; tools are fixed GSQL queries, and the only graph write is the agent's own case
- [x] Case memory is real: each case is written to TigerGraph as an `InvestigationCase` vertex with edges, **read back and verified (20/20)**, and later cases retrieve earlier ones by card or device
- [x] Actions are recommended with routes; `L1`/`L2` actions wait for a human (policy §2), and nothing is executed
- [x] Never falls back to development data: without the official package the runner exits with `ERROR: OFFICIAL HHGOA DATASET UNAVAILABLE` (unit-tested)

### Innovation (15%) — graph + agent + uncertainty + case memory differentiation

- [x] Explicit, first-class uncertainty model distinct from a fraud score (coverage/quality/conflict/completeness, each independently computed)
- [x] GraphRAG-style structured retrieval (not a generic vector RAG) grounding every context item in a real evidence id or case id
- [x] Deterministic case-similarity/recurring-pattern detection reused identically by both the agent's context builder and the API's `/similar` endpoint (no second implementation)
- [x] A benchmark-discovery module that refuses to fabricate results when official data is absent, rather than faking a score
- [x] **Official benchmark:** graph-derived fingerprint for an undocumented device ring (New on 100% of the device's transactions, anonymous proxy, about 2 transactions per card), measured from the bank's own closed cases and used to separate a ring from a popular phone model
- [x] Card-ID derivation rule measured (14,975/14,975 labelled pairs) rather than assumed
- [x] Run history kept, including the runs that were wrong (`run1-summary.json`, `run2-summary.json`) and why

### Demo (10%) — clear, reproducible, complete workflow

- [x] One command per side to start (`uvicorn ... --reload`, `npm run dev`)
- [x] One transaction (`2987937`) exercises the full stack end to end
- [x] Real, non-hardcoded values render in the UI — verified by an integration test that never mocks `lib/api.ts` itself, only the network boundary
- [x] Degraded/error states are also demonstrable and honest, not hidden
- [x] **Official benchmark reproducible with one command:** `python -m app.benchmark.runner --official` (validate → 20 cases → graph write/read-back → conformance → summary); a repeat run gave identical decisions
- [x] 3–5 minute demo video: https://youtu.be/0dqjXyY2Hoc

## Explicitly not claimed

- No official HHGoa **accuracy** score. The official package was obtained and all 20 cases were run (`docs/official-benchmark-report.md`), but it has no answer key. During Phase 2J the package was unavailable (`phase-2-benchmark-report.md`); that record is kept.
- The Phase-2 dashboard/API investigation (development graph, no verdict) is unchanged; the official cases produce `fraud`/`legitimate`/`uncertain` verdicts as the official answer format requires
- No fraud-accuracy metric on either dataset
- No ranking or self-assessed score against other submissions
