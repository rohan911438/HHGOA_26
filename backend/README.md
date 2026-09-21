# HHGoa '26 — Agentic Fraud Investigation Backend

TigerGraph + MCP + GraphRAG + LangGraph. Backend only; no frontend in this phase.

The design principle: **TigerGraph does the data analysis, the LLM does the
reasoning.** The agent never sees raw transaction rows and guesses. It calls
controlled tools, gets structured graph evidence back, and every factual
finding in a recommendation is traceable to a source in the evidence ledger.

```
Trigger -> Investigation -> TigerGraph evidence -> Graph analysis
  -> Policy / prior-case retrieval -> Uncertainty assessment
  -> Additional evidence if needed -> Next-best action -> Case update -> Memory
```

## Status

**Phase 1 of 20: COMPLETE.** See `docs/phase-1-report.md` for the full,
evidence-backed record — every claim below is from an actual command run
against the real dataset and the real live TigerGraph instance.

| Phase | Component | State |
| --- | --- | --- |
| 0 | Repo scaffold, venv, dependencies, config, logging, secret redaction | done, tested |
| 1 | Dataset (Kaggle IEEE-CIS, labeled DEVELOPMENT FALLBACK — not official HHGoa) | ✅ acquired (677MB) + fully inspected (full-file scan) |
| 1 | TigerGraph connection | ✅ host + authentication verified live |
| 1 | Schema design + deployment | ✅ `docs/tigergraph-schema.md`, 5 vertex + 5 edge types, live in `HHGOA_FRAUD` |
| 1 | Development subset | ✅ 5,000 real rows, relationship-preserving sampling, 21.1% fraud vs. 3.5% baseline |
| 1 | Data loaded into TigerGraph | ✅ 6,676 vertices, 15,627 edges, counts verified live |
| 1 | Graph validation | ✅ 6/6 real traversal checks (shared card, shared device, 2-hop, orphans) |
| 1 | TigerGraph MCP (official `tigergraph-mcp` 1.0.3) | ✅ live: starts, authenticates, 37 read-only tools exposed, 0 destructive tools reachable, real schema + vertex-count queries succeed |
| 2+ | Agent, GraphRAG, API, benchmark | not started — awaiting explicit go-ahead |

Six real bugs were found and fixed during Phase 1 by actually running the
tools against live systems, not by inspection — full detail in
`docs/phase-1-report.md` §7–§8. Nothing in this repository claims to work
without a real, logged test run behind it.

Nothing above is claimed as working against TigerGraph until it has actually
run against TigerGraph.

## Setup

```bash
# 1. Create the environment
make install            # or: py -3.11 -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"

# 2. Configure
cp .env.example .env    # then fill in real values - never commit this file

# 3. Verify
make health             # or: python scripts/healthcheck.py
```

`scripts/healthcheck.py` checks configuration, the TigerGraph connection, the
graph schema and vertex counts, and reports each as `ok` / `empty` /
`unavailable` / `not_configured`. It never prints a credential.

## Configuration

Every setting comes from the environment. `.env.example` documents all of
them; `.env` is git-ignored and must never be committed.

Credentials are read in preference order: `TG_API_TOKEN` → `TG_JWT_TOKEN` →
`TG_SECRET` → `TG_PASSWORD`. Whichever is present drives both the application
and the MCP server, which reads the same `TG_*` variables.

Unedited placeholder values (`YOUR_TIGERGRAPH_HOST`, `YOUR_MODEL`, …) are
treated as unset, so a half-filled `.env` reports "not configured" rather than
failing with a confusing DNS error.

### Mock switches

| Variable | Effect when true |
| --- | --- |
| `MOCK_EXTERNAL_ACTIONS` | Customer validation, step-up auth and analyst requests are simulated. No external system is contacted. |
| `MOCK_TIGERGRAPH` | Graph calls are served from local fixtures, so unit tests run with no network. |
| `TG_READ_ONLY` | Mutating GSQL is refused at the client layer. |

## TigerGraph MCP

The official `tigergraph-mcp` package (v1.0.3) exposes 116 tools and reads the
same unprefixed `TG_*` environment variables this project uses, so the MCP
server connects to the same Savanna database with no duplicated credential
handling.

Some of those tools are destructive (`drop_graph`, `clear_graph_data`). The
investigating agent is given **read-only** graph access via the server's own
`TG_ALLOWED_TOOLS` / `TG_BLOCKED_TOOLS` filtering — schema creation and data
loading run through scripts, never through the agent. See `app/mcp/config.py`.

Note the package selects its connection profile with `TG_PROFILE`, not
`TG_MCP_PROFILE`; `app/mcp/config.py` translates between the two.

## Layout

```
app/
  config.py        settings, credential preference, placeholder detection
  logging.py       structured investigation events + credential redaction
  tigergraph/      connection, schema, queries, algorithms, loader, health
  mcp/             MCP server config, client, health
  agent/           LangGraph workflow: state, nodes, controlled tools
  rag/             GraphRAG - graph evidence + document evidence
  cases/           case model, persistence, memory
  policies/        policy loading and evaluation
  evidence/        evidence ledger and scoring
  benchmark/       20-case runner, evaluator, report
scripts/           inspect_dataset, create_schema, load_data, healthcheck, ...
tests/             unit, integration, tigergraph, agent, benchmark
docs/              architecture, tigergraph, agent, evaluation, dataset analysis
```

## Dataset

The HHGOA_IEEE dataset is **not** committed — see `data/README.md` for where
to place it. The graph schema is derived from the dataset as it actually is,
by running:

```bash
python scripts/inspect_dataset.py
```

which writes `docs/dataset-analysis.md` and `artifacts/dataset-profile.json`,
and only ever reads the source files.

## Testing

```bash
make test        # everything
make test-unit   # offline only - no network, no credentials
```

Tests are marked `tigergraph`, `mcp`, `llm` and `integration` so the offline
subset can run anywhere.

## Security

- No credential is ever hardcoded; all come from the environment.
- `.env` is git-ignored (verified).
- The logging layer redacts anything matching a password/secret/token/key
  pattern, plus bearer tokens and `sk-` keys, before it reaches a handler.
- Health endpoints and the config snapshot return a redacted view only.
