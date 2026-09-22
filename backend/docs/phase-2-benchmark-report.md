# Phase 2J Report — Official Benchmark Discovery + Infrastructure Validation

Status date: 2026-09-22. Every result below is from an actual command run in
this session against this repository and, where noted, the live TigerGraph
instance. Nothing here is inferred, assumed, or fabricated.

**OFFICIAL BENCHMARK DATA UNAVAILABLE.**

Per the explicit instruction for this phase: since the official 20-case
HHGoa benchmark was not found, this report does not build a benchmark
adapter, scoring logic, or 20 executed cases against it. It instead
(a) re-verifies the absence, with fresh evidence, and (b) proves the
existing deterministic backend + agent pipeline is wired correctly end to
end, using a small, clearly-labeled diagnostic run against the IEEE-CIS
development-fallback data already in this repository. No number in this
document is presented as, or should be read as, an official HHGoa
benchmark score.

---

## 1. Discovery (2J.1)

Re-ran the official-dataset search this phase, independent of
`docs/phase-1-report.md`'s original 2026-09-21 search:

| Check | Result |
| --- | --- |
| `backend/data/hhgoa/` contents | `.gitkeep`, `train_transaction.csv`, `train_identity.csv` only — the IEEE-CIS Kaggle files, not an HHGoa-specific layout |
| `CASES_PATH` (`data/hhgoa/cases/`) | does not exist |
| `POLICIES_PATH` (`data/hhgoa/policies/`) | does not exist |
| `BENCHMARK_PATH` (`data/hhgoa/benchmark/`) | does not exist |
| Whole `C:\Users\dell` tree, fresh `find` for `*hhgoa*` | only browser cache / `.lnk` shortcuts / a stale `Recent` link to a since-deleted `Desktop\HHGOA` folder — no dataset, no case files, no policy documents |
| `Downloads` for `*benchmark*`, `*fraud_polic*`, `*typolog*` | nothing found |
| TigerGraph live schema (`HHGOA_FRAUD` graph) | unchanged from Phase 1 — vertex/edge types match the IEEE-CIS-derived schema in `docs/tigergraph-schema.md`, not an official HHGoa schema |

This reproduces and confirms `docs/phase-1-report.md` §1's finding: **no
official HHGoa dataset, case files, policy material, fraud-typology
documents, or 20-case benchmark exist anywhere in this repository or
development environment.** The one unresolved lead from that report (a
`Desktop\HHGOA` shortcut pointing to a folder that no longer exists on
disk) remains unresolved — nothing new was found this session either.

This is now enforced in code, not just prose: `app/benchmark/discovery.py`
implements `discover_official_benchmark()`, which any future run of this
phase (or the API layer, later) can call to get the same honest answer
programmatically. Its own output, captured live in this session:

```json
{
  "official_dataset_available": false,
  "path_exists": {
    "dataset_root": true,
    "cases_path": false,
    "policies_path": false,
    "benchmark_path": false
  },
  "case_files_found": [],
  "policy_files_found": [],
  "benchmark_files_found": [],
  "dataset_root_contents": [".gitkeep", "train_identity.csv", "train_transaction.csv"]
}
```

**Cases discovered: 0.**

## 2. What was NOT built, and why (2J.2–2J.3)

Per the instruction not to fabricate official material, this phase does
**not** contain:

- 20 benchmark cases (none exist in this source)
- expected answers / a scoring rubric (none exist in this source)
- a parser for the official file format (the format itself was never
  seen — `app/benchmark/discovery.py::load_official_benchmark_cases()`
  raises `NotImplementedError` even in the hypothetical case where a file
  later appears under `CASES_PATH`, rather than guessing its schema)
- any official fraud typology, bank policy, or regulatory threshold

`app/benchmark/models.py` does define `BenchmarkCase`,
`BenchmarkExpectedAnswer`, and `BenchmarkResult` (2J.2/2J.3's typed
models), and `ComparisonCategory`/`ComparisonOutcome` for the eight
comparison categories 2J.3 specifies — but these are load-bearing only
once real official files exist. `BenchmarkExpectedAnswer` has no default
values that could be mistaken for a real answer: every field is
`None` unless a real source populates it.

`load_official_benchmark_cases()` raises
`app.benchmark.discovery.OfficialBenchmarkUnavailable` rather than
silently falling back to the IEEE-CIS data — tested in
`tests/unit/test_benchmark_discovery.py::TestLoaderNeverFabricates`.

## 3. What was built instead: infrastructure validation (2J.4)

Per the instruction to "continue only with infrastructure validation,"
`app/benchmark/runner.py::run_infrastructure_validation()` exercises the
real, already-verified pipeline end to end:

```
transaction_id -> AgentOrchestrator.run() -> AgentInvestigationResult
```

against **real TigerGraph data** (the IEEE-CIS development-fallback
subset loaded in Phase 1, the same graph every other live test in this
repo uses), using the project's existing `FakeLLMClient` — a
deterministic test double, not a real LLM call, matching the pattern
`tests/tigergraph/test_agent_live.py` already established for live
wiring checks without requiring OpenAI credentials (which are, in fact,
not configured in this environment — `llm_configured: False`).

Every `BenchmarkResult` produced this way has
`"is_official_benchmark_case": false` — the field a caller must check
before treating any of this as a scored outcome.

### 3a. Live run, 2026-09-22 (`scripts/run_benchmark_infra_validation.py`)

5 transactions from the development-fallback subset (`data/dev/`,
already loaded into the live `HHGOA_FRAUD` graph): one already used as
`KNOWN_TXN` by earlier live tests (`2987937`), plus the first four rows
of `data/dev/transactions_dev.csv` (`2987000`–`2987003`) — a
deterministic selection, not cherry-picked for a favorable result.

```json
{
  "is_official_benchmark": false,
  "official_dataset_available": false,
  "cases_executed": 5,
  "cases_completed": 5,
  "cases_failed": 0,
  "failed_transaction_ids": []
}
```

Full per-case detail (evidence, uncertainty, policy decision, case
status, timings) is in `benchmark/results/results.json` and
`benchmark/results/per_case/<transaction_id>.json`, generated by this
exact run — nothing in those files was hand-written.

Every case: `investigation_status=COMPLETED`, `uncertainty_level=LOW`,
`recommended_action=CREATE_CASE`, `approval_required=true`,
`approval_route=ANALYST`, `case_status=PENDING_REVIEW`, 1 iteration, 1
tool-call batch (6 underlying tool calls per investigation), 5.9s–8.0s
end to end (dominated by live TigerGraph round-trips — see §5).

This demonstrates the full chain the challenge asks for:

```
trigger -> investigation -> graph evidence -> uncertainty ->
historical context -> agent reasoning -> policy/NBA ->
approval route -> case record -> final explanation
```

is genuinely wired and working against real graph data. It says nothing
about fraud-detection accuracy, because there is no official label or
expected answer here to be accurate against — see §4.

## 4. Scoring (2J.6)

**No benchmark score is computed.** There is no official expected
answer for any of the 5 diagnostic transactions above, so nothing here
is scored as correct/incorrect. The only numbers reported are the
project diagnostic metrics in §3a (cases executed/completed/failed,
timings) — clearly labeled `is_official_benchmark: false` in both the
JSON output and this document. If the official 20-case benchmark and its
expected answers become available, `ComparisonOutcome` (2J.3) and a real
scoring pass over `BenchmarkExpectedAnswer` are the next steps — neither
is implemented against invented data.

## 5. Failure analysis (2J.7)

No case failed in the live run (§3a: 0/5 failed). The regression run
below also passed in full. There is therefore no root-cause table to
fill in this session — this section exists to record that explicitly
rather than omit it, and will be populated for real once either (a) a
failure occurs, or (b) the official benchmark exists and produces
mismatches to analyze.

One observation, not a failure: end-to-end latency per case is
5.9–8.0 seconds, dominated by live TigerGraph query round-trips
(`find_shared_email_activity` and `find_shared_address_activity` were
consistently the slowest calls, 0.8–2.3s each against the current
40k-row development subset) — noted for Phase 2M's performance pass, not
addressed here per the instruction not to optimize prematurely.

## 6. Regression (2J.8)

Full offline unit suite:

```
302 passed in ~6-11s
```

(297 pre-existing + 5 new: `tests/unit/test_benchmark_discovery.py`.)
One pre-existing test,
`test_investigation_service.py::TestOverallTimeout::test_overall_timeout_marks_slow_tools_partial_without_hanging`,
failed once when run under full-suite timing pressure and passed both in
isolation and on a full-suite re-run — a real-time-clock-based test that
is occasionally sensitive to CPU contention on this machine, not a
regression introduced by this phase. Recorded here per the instruction
not to hide environmental flakiness; the underlying logic was not
touched.

Full live (`-m tigergraph`) suite:

```
76 passed in ~5.5 min
```

(72 pre-existing + 4 new: `tests/tigergraph/test_benchmark_live.py`.)

`ruff check` on every new file: clean.

## 7. Final report

- **Official dataset available:** NO
- **Cases discovered:** 0
- **Cases executed (official):** 0 — none exist to execute
- **Cases executed (diagnostic infrastructure validation):** 5
- **Cases successfully completed (diagnostic):** 5 / 5
- **Benchmark score:** not computable — no official expected answers exist
- **Diagnostic metrics (project-authored, not an HHGoa score):** 5/5
  pipeline runs completed end to end against live TigerGraph data;
  0 failures; 5.9–8.0s per case
- **Failed cases:** none
- **Root causes:** none to report

### What this phase adds to the codebase

- `app/benchmark/` — `models.py` (typed adapter models), `discovery.py`
  (honest search + fail-closed loader), `runner.py` (diagnostic
  infrastructure-validation runner)
- `tests/unit/test_benchmark_discovery.py` — 5 tests, offline
- `tests/tigergraph/test_benchmark_live.py` — 4 tests, live
- `scripts/run_benchmark_infra_validation.py` — regenerates
  `benchmark/results/{results,summary}.json` and `per_case/*.json`
- `benchmark/results/` — the actual output of the run in §3a

### What this phase does NOT add

- No 20 benchmark cases, no expected answers, no official score, no
  official fraud typology/policy/regulatory content. If/when the real
  HHGoa dataset is located, `app/benchmark/discovery.py` will detect it
  automatically (its checks are already wired to `CASES_PATH` /
  `POLICIES_PATH` / `BENCHMARK_PATH`), and the real parser +
  expected-answer comparisons described in 2J.2/2J.3/2J.6 should be built
  against the real files at that point — not before.

**STOP. Phase 2J complete. Do not begin Phase 2K until explicitly
approved.**
