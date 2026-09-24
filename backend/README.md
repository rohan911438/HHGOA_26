# HHGoa '26 — Agentic Fraud Investigation Backend

TigerGraph + MCP + LangGraph + FastAPI. See `../README.md` for the
project-level overview (architecture diagram, demo, safety boundaries,
data limitation) and `docs/phase-2-frontend.md` for the Next.js frontend
this backend serves.

**Design principle: TigerGraph and a stack of deterministic services do
the data analysis; the LLM orchestrates and explains.** The agent never
recalculates evidence coverage, uncertainty, policy authorization, or
case-similarity scores — those are computed once, deterministically, by
components that are independently unit- and live-tested. The agent's
only job is to decide *when* to call them, interpret their output, and
produce a grounded, evidence-cited explanation. Every factual claim in a
recommendation is traceable to a real evidence ID.

```
TigerGraph (Savanna)
   v
Investigation Queries        (app/tigergraph/queries.py)          — 6 read-only GSQL queries
   v
Evidence Model                (app/evidence/)                      — normalized, quality-rated, provenance-tracked
   v
Tool Registry                 (app/investigation/registry.py)      — the only surface an agent may call; no raw GSQL
   v
InvestigationService           (app/investigation/service.py)       — runs the 6-tool plan, sequential or bounded-concurrent
   v
InvestigationSnapshot
   v
UncertaintyEngine              (app/uncertainty/)                   — coverage / quality / conflict / completeness, deterministic
   v
UncertaintyAssessment
   v
Policy / Next-Best-Action      (app/policy/)                        — recommendation + approval routing, deterministic
   v
PolicyDecision
   v
CaseManager                    (app/case/)
   /              \
CaseStore       CaseMemory     — historical retrieval, deterministic similarity
                     v
              Historical Cases
   ^
   |  (all of the above, composed — never replaced — by:)
   |
Agent / LLM Orchestrator        (app/agent/, LangGraph)             — orchestration, interpretation, explanation only
```

## Status

**Phases 0 – 2L: COMPLETE** (backend + agent + API + frontend). Phase 2M
is final submission-readiness verification — see `../README.md`'s
Status section for the exact, currently-observed pass/fail numbers
(including current live-TigerGraph availability, which fluctuates with
the shared Savanna workspace). Every phase below has an evidence-backed
writeup in `docs/` — every claim is from an actual command run against
the real dataset and the real live TigerGraph instance, never from
inspection alone.

| Phase | Component | Offline tests | Live tests | Doc |
| --- | --- | --- | --- | --- |
| 0 | Repo scaffold, venv, dependencies, config, logging, secret redaction | — | — | `docs/phase-1-report.md` |
| 1 | Dataset acquisition + inspection, TigerGraph schema + load, MCP setup | — | ✅ connection, schema, 6,676 vertices / 15,627 edges, MCP 37 read-only tools | `docs/phase-1-report.md`, `docs/tigergraph-schema.md` |
| 2A | 6 investigation queries (`app/tigergraph/queries.py`) | — | 13/13 | `docs/phase-2-graph-analysis.md` |
| 2B | Evidence model — quality, provenance, dataset-label separation | 57/57 | 6/6 | `docs/phase-2-evidence-model.md` |
| 2C | Investigation tool registry — explicit allowlist, timeouts, no raw GSQL | 64/64 | 9/9 | `docs/phase-2-tool-registry.md` |
| 2D | Deterministic investigation service — sequential/bounded-concurrent, ~3.4x speedup | 18/18 | 4/4 | `docs/phase-2-investigation-service.md` |
| 2E | Uncertainty engine — coverage/quality/conflict, ~0.11ms/call | 31/31 | 11/11 | `docs/phase-2-uncertainty-model.md` |
| 2F | Policy / next-best-action engine — recommendation ≠ authorization, ~0.02ms/call | 30/30 | 9/9 | `docs/phase-2-policy-nba.md` |
| 2G | Case management + case memory — deterministic similarity, recurring patterns | 39/39 | 7/7 | `docs/phase-2-case-management-memory.md` |
| 2H | Agent orchestrator (LangGraph) — orchestrates, never replaces, the above | 24/24 | 6/6 | `docs/phase-2-agent-orchestrator.md` |
| 2I | GraphRAG / context layer — structured retrieval, no vector DB | included below | included below | `docs/phase-2-graphrag-context.md` |
| 2J | Official-benchmark discovery + infrastructure validation (0 official cases found; not fabricated) | 5/5 | 5/5 diagnostic runs | `docs/phase-2-benchmark-report.md` |
| 2K | FastAPI serving layer — 9 routes, typed models, dependency injection | 27/27 (API) | 3/3 | `docs/phase-2-api.md` |
| 2L | Next.js frontend — investigation console | 29/29 | see `docs/phase-2-frontend.md` | `docs/phase-2-frontend.md` |
| Final | Official HHGOA_IEEE benchmark: package validation, `HHGOA_IEEE` graph load, 20-case run, graph write + read-back, conformance | 27/27 | 20/20 cases executed, written and verified | `docs/official-benchmark-report.md` |
| **Backend total (unit + API)** | | **365/365** | **79/79** (last fully-healthy run) | |

A separate, fully optional real-LLM integration test
(`tests/integration/test_agent_real_llm.py`) skips cleanly with no
credential configured — never part of the numbers above, and never
allowed to affect deterministic-suite health (see
[LLM configuration](#llm-configuration)).

### Known environmental behavior

The live TigerGraph tests hit a **shared Savanna workspace** repeatedly
across a long session. Under sustained load, individual live tests have
intermittently failed with a transient network symptom (a data-race in a
consistency check, or a one-off DNS resolution blip) and then passed
cleanly on immediate re-run in isolation, every time, across this
project's entire Phase 2 development. This is treated as an
infrastructure characteristic, not a defect — the project's own
diagnostics tooling (`scripts/test_tigergraph.py`) exists specifically
to distinguish "the code is wrong" from "the shared environment is
briefly unavailable," and application logic is never altered merely to
mask this.

## Setup

```bash
# 1. Create the environment
make install            # or: py -3.11 -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"

# 2. Configure
cp .env.example .env    # then fill in real values - never commit this file

# 3. Verify
make health             # or: python scripts/healthcheck.py
python scripts/test_tigergraph.py --json   # detailed, classified connection diagnostics
```

`scripts/healthcheck.py` checks configuration, the TigerGraph connection,
the graph schema, and vertex counts, reporting each as `ok` / `empty` /
`unavailable` / `not_configured`. `scripts/test_tigergraph.py` goes
further: it classifies a failure as `CONFIGURATION` / `AUTHENTICATION` /
`NETWORK` / `ENDPOINT` / `GRAPH` / `SDK` / `SERVER`, and correctly fails
loudly on an HTTP 5xx rather than reporting a false pass. Neither ever
prints a credential.

## Configuration

Every setting comes from the environment. `.env.example` documents all
of them; `.env` is git-ignored and must never be committed.

TigerGraph credentials are read in preference order: `TG_API_TOKEN` →
`TG_JWT_TOKEN` → `TG_SECRET` → `TG_PASSWORD`. Whichever is present
drives both the application and the MCP server, which reads the same
`TG_*` variables. Unedited placeholder values (`YOUR_TIGERGRAPH_HOST`,
`YOUR_MODEL`, …) are treated as unset, so a half-filled `.env` reports
"not configured" rather than failing with a confusing DNS error.

### LLM configuration

`OPENAI_API_KEY` / `OPENAI_MODEL` are **optional**. The entire
deterministic backend — including all 263 offline and 65 live tests
above — runs with no LLM credential at all, using `FakeLLMClient`
(`app/agent/llm.py`), a deterministic, scripted test double. The real
provider (`OpenAIChatClient`, built on `langchain_openai.ChatOpenAI`)
is constructed lazily and **fails loudly** (`LLMNotConfiguredError`) if
no credential is present — it never silently substitutes a fake client
or a different provider. See `docs/phase-2-agent-orchestrator.md` §6.

### Mock switches

| Variable | Effect when true |
| --- | --- |
| `MOCK_EXTERNAL_ACTIONS` | Customer validation, step-up auth, and analyst-review requests are represented as controlled stubs (`RequestedEvidence`, Phase 2H) — no external system is contacted, and nothing is ever claimed to have been obtained. |
| `MOCK_TIGERGRAPH` | Reserved for future fixture-backed offline runs; the current test suite instead mocks at the `app.tigergraph.queries` function boundary directly (see [Testing](#testing)). |
| `TG_READ_ONLY` | Mutating GSQL is refused at the client layer (`app/tigergraph/client.py`). |

## TigerGraph MCP

The official `tigergraph-mcp` package (v1.0.3) reads the same
unprefixed `TG_*` environment variables this project uses, so the MCP
server connects to the same Savanna database with no duplicated
credential handling. Some of its tools are destructive
(`drop_graph`, `clear_graph_data`, `drop_all_data_sources`) — the
project exposes only the **read-only** subset via the server's own
`TG_ALLOWED_TOOLS` / `TG_BLOCKED_TOOLS` filtering
(`app/mcp/config.py::INVESTIGATION_TOOLSET`, `ALWAYS_BLOCKED`); schema
creation and data loading run through scripts, never through the agent.

**The agent does not talk to MCP directly, and does not receive the 37
read-only tools individually.** Its only TigerGraph-facing surface is
`InvestigationService.investigate_transaction()`
(`app/investigation/service.py`), which internally runs the six
explicitly-registered investigation tools (`app/investigation/tools.py`)
through the tool registry (`app/investigation/registry.py`) — no raw
GSQL, no arbitrary MCP invocation, ever reachable from an LLM. This
boundary is enforced by AST-level tests, not just convention — see
`docs/phase-2-tool-registry.md` §11 and `docs/phase-2-agent-orchestrator.md` §5.

Note: the package selects its connection profile with `TG_PROFILE`, not
`TG_MCP_PROFILE`; `app/mcp/config.py` translates between the two.

## Layout

```
app/
  config.py         settings, credential preference, placeholder detection
  logging.py        structured investigation events + credential redaction
  tigergraph/        connection, schema, queries, diagnostics, health, loader
  mcp/                MCP server config (read-only filtering)
  evidence/           evidence model — quality, provenance, dataset-label isolation
  investigation/      tool registry + deterministic investigation service
  uncertainty/         coverage / quality / conflict / completeness engine
  policy/               next-best-action recommendation + approval routing
  case/                  case management + deterministic case-memory retrieval
  agent/                  LangGraph orchestrator: state, LLM abstraction, prompts, graph
  context/                GraphRAG/context layer (Phase 2I) - structured retrieval, no vector DB
  benchmark/               official-benchmark discovery + infra-validation runner (Phase 2J)
    official/                 official HHGOA_IEEE benchmark (`python -m app.benchmark.runner --official`)
  api/                       FastAPI serving layer (Phase 2K) - routes, models, dependency injection
  rag/, cases/, policies/    empty scaffolds - unrelated pre-existing placeholders, not built
scripts/            inspect_dataset, create_schema, load_data, healthcheck,
                     test_tigergraph (classified live diagnostics), validate_graph
tests/
  unit/             offline - no network, no credentials, no real LLM
  tigergraph/       live - requires a real TigerGraph connection (marker: tigergraph)
  integration/       optional real-LLM integration test (markers: llm, tigergraph)
docs/               one evidence-backed report per phase (see the Status table above)
```

`app/policies/` (plural) is a separate, empty, pre-existing scaffold
from Phase 0 — unrelated to `app/policy/` (singular, Phase 2F), left
untouched and reserved for a possible future policy-document/RAG phase.

## Dataset

> **Official package:** the official HHGOA_IEEE dataset (`data/hhgoa_ieee/`, fetched with
> `python -m app.benchmark.official fetch`) powers the 20-case benchmark in its own graph,
> `HHGOA_IEEE`. See `docs/official-benchmark-report.md`. The rest of this section describes the
> development fallback that the Phase 1–2M API and dashboard still use.

The current dataset is **IEEE-CIS Fraud Detection**, an explicitly
labeled **DEVELOPMENT FALLBACK — not official HHGoa data**. It is not
committed — see `data/README.md` for where to place it. It provides
only a binary `isFraud` label, never an official continuous risk score,
never official HHGoa case history, and never official policy
thresholds. Every phase in this project that could be tempted to use
`isFraud` as a shortcut — the uncertainty engine, the policy engine, the
case-similarity formula, and the agent orchestrator — is instead
independently verified, both behaviorally and via static (AST) source
inspection, to **never read that label at all**. This is the project's
single most-tested cross-cutting property; see any `docs/phase-2-*.md`
file's "label isolation" section for the specific proof.

The graph schema is derived from the dataset as it actually is, by
running:

```bash
python scripts/inspect_dataset.py
```

which writes `docs/dataset-analysis.md` and `artifacts/dataset-profile.json`,
and only ever reads the source files.

## Testing

```bash
make test          # everything pytest can discover
make test-unit      # offline only - no network, no credentials, no real LLM
make test-tg         # live - requires a real, reachable TigerGraph connection
pytest -m llm         # optional real-LLM integration test - skips cleanly with no credential
```

Tests are marked `tigergraph`, `mcp`, `llm`, and `integration` so the
offline subset (`pytest tests/unit tests/api`) runs anywhere, including
CI with no external credentials at all. Live tests use this project's
own diagnostics (`app.tigergraph.diagnostics.run_checks`) to
`pytest.skip` cleanly — not fail — when TigerGraph is not currently
reachable, rather than reporting a false failure for an infrastructure
gap. Every live-test file follows this convention (as of Phase 2M,
including `tests/tigergraph/test_investigation_queries.py`, the one
Phase 2A file that predated it and has since been brought in line).

Frontend tests (`frontend/`, Jest + React Testing Library) are separate
- see `docs/phase-2-frontend.md`'s Testing section; run with `npm test`
inside `frontend/`.

Every deterministic component (evidence normalization, the uncertainty
formula, the policy heuristic, case similarity, the agent's control-flow
routing) has a dedicated **architectural-boundary test** that parses its
own module's source with Python's `ast` module and asserts it never
imports `app.tigergraph.queries` directly and never references the
dataset's label fields — a stronger guarantee than a docstring claim.

## Security

- No credential is ever hardcoded; all come from the environment.
- `.env` is git-ignored (verified after every phase in this project).
- The logging layer redacts anything matching a password/secret/token/key
  pattern, plus bearer tokens and `sk-` keys, before it reaches a handler
  (`app/logging.py::RedactionFilter`).
- No `eval`/`exec`/arbitrary shell execution anywhere in the codebase —
  verified by AST scan for the agent package specifically
  (`app/agent/`, the one place an LLM's output reaches this project's
  code), since that is the component most exposed to adversarial input.
- The agent's only LLM-controlled surface is a strict, enum-constrained,
  `extra="forbid"` Pydantic schema (`LLMDecision`) — no free-form
  instruction the model produces (or that appears inside graph data it
  is shown) can reach executable code. See
  `docs/phase-2-agent-orchestrator.md` §6 for the full anti-prompt-
  injection design.
- **Known, documented, unresolved issue**: `pyTigerGraph` 2.0.4
  unconditionally disables TLS certificate verification for any HTTPS
  host (root-caused in `docs/phase-2-evidence-model.md` §8). The
  connection is genuinely encrypted but the certificate chain is never
  validated — a real MITM exposure on an adversarial network. A
  documented workaround attempt was inconclusive (confounded by
  TigerGraph token-endpoint rate limiting during testing) and was not
  applied, rather than risk an unsafe fix. Tracked, not suppressed.

## No fraud verdict, anywhere

No model in this codebase has a `fraud_probability`, `fraud_verdict`,
`final_fraud_score`, or `agent_confidence` field — checked by a
dedicated regression test in every phase from 2B onward. This project
computes **evidence, coverage, uncertainty, and recommendations** — a
final fraud determination is explicitly out of scope for every
component built so far, deterministic or LLM-orchestrated alike.
