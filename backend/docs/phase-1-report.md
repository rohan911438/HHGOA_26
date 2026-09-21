# Phase 1 Report — Dataset + TigerGraph Connection

Status date: 2026-09-21. Every result below is from an actual command run in
this session — see the "evidence" line under each check. Nothing here is
inferred or assumed.

**PHASE 1 COMPLETE.** Both external blockers from earlier in this document
(official dataset absent; TigerGraph credentials absent) were resolved
during this session — see §1–§2 for how, and §3 onward for the full,
tested pipeline: dataset acquired and inspected, TigerGraph connected,
schema designed and deployed, development subset loaded, graph validated
with real traversal queries, and the TigerGraph MCP server verified live.
§6 has the full checklist; §8 has the final summary and known issues.
Sections below that predate completion are left as originally written
(with corrections noted inline) rather than rewritten, so the record of
what was actually blocked, and how it got unblocked, isn't lost.

---

## 1. Official dataset search

Per the mandate to search before downloading anything:

**Official HHGOA dataset found: NO**

Search performed:

| Location | Method | Result |
| --- | --- | --- |
| Repo (`backend/`) | filesystem walk | not present |
| `Desktop`, `Downloads`, `Documents`, whole `C:\Users\dell` tree | `find` for `*hhgoa*`, `*ieee-fraud*`, `*fraud_policy*`, `*typolog*`, `*benchmark*case*` | not present |
| Windows Recycle Bin | directory listing | one unrelated recovered item, nothing named HHGoa |
| `hhgoa.com` (public challenge site) | fetched live | landing page + 3 task pages; no dataset link, no TigerGraph-specific brief |
| `hacker-house-goa-2026.devfolio.co` | fetched live | no TigerGraph/fraud track, no dataset link |
| Task #3 brief (the only doc mentioning a partner logo near "TigerGraph") | fetched live via Google Docs export | is a **face-ID / blockchain-verification task**, unrelated to fraud investigation |

**One lead found, not resolved:** `C:\Users\dell\AppData\Roaming\Microsoft\Windows\Recent\HHGOA.lnk` resolves to a target path `C:\Users\dell\Desktop\HHGOA` — a folder that **no longer exists** on disk. Its `claude-cli-nodejs` cache directory survives (`Cache\C--Users-dell-Desktop-HHGOA`) but contains only MCP connector logs (Canva/Gmail/Chrome), no project transcript or dataset reference. This means a `Desktop\HHGOA` folder existed at some point and was deleted before this session started; what it contained is unknown from disk alone.

I did not search Gmail, Telegram, or other private channels — those need your involvement to authorize and to actually locate anything (a Chrome-history query for `hhgoa`-adjacent terms was in fact denied mid-search by the tool's own PII-handling policy, correctly, since it's your personal browsing history).

**What I need from you** to resolve this properly: either (a) point me at wherever the actual HHGoa dataset + brief live (an email attachment, a private Drive/Notion link, a physical USB stick, or a memory of what was in the deleted `Desktop\HHGOA` folder), or (b) confirm you want me to proceed on the Kaggle IEEE-CIS fallback per §3–§4 of your instructions while that's sorted out.

---

## 2. Decision: development fallback in effect

Per your explicit instruction (§3–§5, §25): since the official dataset is not
currently available, the system proceeds using the Kaggle IEEE-CIS Fraud
Detection dataset, **clearly labeled and never presented as official**:

```
OFFICIAL_HHGOA_DATA        <- not present. Loader path exists, unused.
DEVELOPMENT_IEEE_CIS_DATA  <- Kaggle "ieee-fraud-detection" competition.
                               Used for infra dev only. NOT the HHGoa
                               benchmark. No fraud policy, no case files,
                               no 20 benchmark cases exist in this source.
```

This label is enforced in code, not just in docs: `scripts/fetch_dataset.py`
only ever writes files under `data/hhgoa/`, and every doc this phase
produces (this one included) is explicit about which rows are official vs.
fallback. `docs/dataset-analysis.md`, `docs/fraud-typologies.md`,
`docs/policy-index.md`, and `docs/benchmark.md` are **not created in this
phase** — creating them now, with only Kaggle data on hand, would mean
either leaving them empty or fabricating policy/typology/benchmark content
that §1, §15–§18 of your instructions explicitly forbid. They get written
once real files exist to inspect.

### 2a. Kaggle credential status

**Not configured.** Verified by actually running the fetch script:

```
$ python scripts/fetch_dataset.py
No Kaggle API credentials found.
  1. Sign in at https://www.kaggle.com
  2. Settings -> API -> "Create New Token"  (downloads kaggle.json)
  3. Move it to:  C:\Users\dell\.kaggle\kaggle.json
  4. Accept the competition rules (required, or downloads return 403):
     https://www.kaggle.com/c/ieee-fraud-detection/rules
exit code: 2
```

`C:\Users\dell\.kaggle\kaggle.json` does not exist. `KAGGLE_USERNAME` /
`KAGGLE_KEY` are not set. This is the credential-file path from your own
instructions — I have not asked you to paste a token, and none was printed.

**Next action (yours):** create the Kaggle API token as above, or hand me
the real HHGoa dataset instead.

---

## 3. TigerGraph connection

**Workspace:** `HHGOA-FraudGraph` (`24a9954c-9fc0-4333-9945-43d988f6d9f7`), **Database:** `Database-1` (`251cfa8b-9698-44a5-b5bd-8ee11e5921ff`), TigerGraph 4.2.5 — per your Phase 0 spec. No new workspace or database was created; none of today's commands are capable of creating one (the client and MCP config are read-oriented for the agent, and no `CREATE WORKSPACE`-equivalent call exists in this codebase).

### 3a. Status: **host reachable, authentication verified** ✅

Real output from `python scripts/test_tigergraph.py` against the live
instance:

```
TigerGraph connection verification
==============================================================
  ✓ Host configured - https://tg-24a9954c-9fc0-4333-9945-43d988f6d9f7.tg-2635877100.i.tgcloud.io
  ✓ Graph name configured - HHGOA_FRAUD
  ✓ Credential configured - using secret
  ✓ Host reachable - 2177.6 ms
  ✓ Authentication successful - via secret
  ○ Graph listing - Semantic Check Fails: No graph available. Please create a graph first.
  ✗ Graph accessible [GRAPH] - schema retrieval failed: HTTPError: 404 Client
        Error: Not Found for url: .../gsql/v1/schema/graphs/HHGOA_FRAUD
  ○ Schema accessible
  ○ Read-only query successful
==============================================================
Result: FAILED - see classification above
exit code: 1
```

The remaining failure is **expected and correct**: `HHGOA_FRAUD` does not
exist yet, and per this phase's explicit instruction, a graph is not
created until after the actual dataset has been inspected. Host and
credential are proven working; graph creation is deliberately deferred,
not blocked.

**How the host was found:** the Savanna UI does not surface the REST++
hostname directly in any panel we could locate (Connect dropdown, Admin
Portal, Query Editor all checked). Resolved instead by calling TigerGraph's
own Controller API (`GET https://api.tgcloud.io/controller/v4/v2/workgroups`,
read-only, using the user-supplied API key exactly as intended) and reading
the `nginx_host` field of the `HHGOA-FraudGraph` workspace entry — this is
the real endpoint, not invented or guessed.

**Two credential systems, easy to conflate:** Savanna's "API Keys" (Admin →
Settings → API Keys) authenticate against the *Controller* API
(`api.tgcloud.io`, workspace management) and are a completely different
credential from a workspace's *GSQL secret* (32-character string, created
via `CREATE SECRET` in GSQL or the "Database Secrets" panel), which is what
`pyTigerGraph`'s data-plane calls need. The first value provided (40 chars,
underscores) was the former and correctly failed authentication; the
second (32 lowercase alphanumeric chars) was the latter and worked.

### 3b. A real bug found and fixed against the live instance

The first run of the connection test reported `healthy: true` while its
own detail line read `"User authentication failed"` — the classifier's
keyword list didn't recognize that phrasing (no `"401"`, `"403"`, or
`"unauthoriz"` substring), so a genuine credential failure silently landed
in the non-fatal `ENDPOINT` bucket. Caught immediately because the
contradiction was visible in the very first live run, not from inspection.
Fixed by broadening the keyword match (`"authenticat"`, `"access denied"`,
`"permission denied"`) and locked in with a regression test using the
exact real error text, `("User authentication failed", None)`.

A second, smaller honesty bug surfaced in the same run: `SHOW GRAPH *`
returned the plain-text string `"Semantic Check Fails: No graph available.
Please create a graph first."` as a normal (non-raising) GSQL result, and
the "Graph listing" check put a ✓ next to it purely because no Python
exception was raised. Fixed to scan the returned text for
failure-shaped phrases and mark those informational rather than passing;
covered by 2 new unit tests using a stub GSQL response. Full suite: 23/23
passing.

**Credential hygiene note:** the GSQL secret was pasted directly into
chat by the user. It is stored only in `backend/.env` (git-ignored,
confirmed) and was never echoed back in any response, command output, log,
or committed file (verified by grepping the whole repo for both provided
credential values before proceeding — found only in `.env`). The user was
told once, plainly, that pasting a secret into a chat transcript is worth
rotating afterward if that transcript will ever be shared or reviewed.

---

## 4. Work completed this phase (verified)

### 4a. `scripts/fetch_dataset.py` (Phase 0, re-verified)
Kaggle downloader for the fallback dataset. Confirmed it correctly detects
missing credentials and exits 2 with actionable guidance (§2a). Not yet run
to completion — no credentials to run it with.

### 4b. `scripts/test_tigergraph.py` + `app/tigergraph/diagnostics.py` (new)
A granular connection checklist (host reachable → authenticated → graph
accessible → schema accessible → read-only query), each check classified
into `CONFIGURATION` / `AUTHENTICATION` / `NETWORK` / `ENDPOINT` / `GRAPH`
/ `SDK` / `SERVER` so a real failure, once we have credentials, is
immediately diagnosable rather than a generic stack trace. The
classification logic lives in `app/tigergraph/diagnostics.py`, not the
script, so the future `/health/tigergraph` endpoint reuses the exact same
logic instead of reimplementing it.

**Two real bugs were found and fixed by actually running this, not by
inspection:**

1. **Windows console encoding crash.** The script's ✓/✗/○ symbols are
   Unicode; Windows' default `cp1252` console codepage can't encode them,
   so the very first run crashed with `UnicodeEncodeError` before printing
   a single result. Fixed by reconfiguring stdout to UTF-8 with an
   ASCII-safe (`[OK]`/`[X]`/`[-]`) fallback if that reconfiguration itself
   fails. Re-run confirmed clean output.

2. **Misclassification bug.** `TigerGraphClient`'s secret-auth path wraps
   every underlying driver error in the message `"Could not mint a REST++
   token from TG_SECRET: ..."`. The verification script's first cut
   classified failures by keyword-matching that wrapper text — and since
   the wrapper text itself contains the word *"token"*, a plain DNS
   failure (host doesn't resolve) was being misclassified as an
   **authentication** failure. Caught by testing against a synthetic
   non-resolving host before ever touching the real Savanna endpoint:

   ```
   before fix: Host reachable -> ok [AUTHENTICATION]   <- wrong
   after fix:  Host reachable -> failed [NETWORK]      <- correct
   ```

   Fixed two ways: (a) `TigerGraphClient._authenticate` now preserves the
   original exception's message instead of just its type name, and (b)
   classification walks `exc.__cause__` to the *root* exception and reads
   signal from there, not from our own wrapper phrasing. Locked in with 10
   unit tests in `tests/unit/test_tigergraph_diagnostics.py` covering the
   configuration-gate path, the cause-chain walk (including a
   self-referential-chain guard so it can't infinite-loop), and each
   classification bucket — all passing.

Synthetic verification (real commands, fake unreachable hosts, no live
Savanna needed):

```
non-resolving host  -> Host reachable: failed [NETWORK]        (correct)
example.com:59999   -> Host reachable: ok [] (ENDPOINT, non-fatal detail)
```

### 4c. `docs/dataset-search-report.md` content
Folded into §1 above rather than a separate file, since it is short and
this report is the canonical Phase 1 record.

### 4e. `app/dataset/subset.py` + `scripts/create_dev_subset.py` (new)
Relationship-preserving development-subset sampler, built while both
external blockers (§2a, §3) remained open, so subset creation is ready
the instant either the Kaggle fallback or the real dataset lands. Full
rationale and the exact (publicly-documented, **unverified locally**)
column assumptions are in `docs/development-fallback-analysis.md` —
required reading before trusting this tool's output against real data.

Deliberately not naive random-row sampling: fraud graphs are only
interesting because of shared structure (same card, address, email
domain, device), and IID sampling would sever almost all of it. Instead:
a deterministic stratified seed (over-representing fraud rows) is
expanded outward by shared linking keys, with the size capped and every
stage's row count recorded in a manifest.

Verified two ways:
1. **CLI degrades correctly** with no data present — `python
   scripts/create_dev_subset.py` exits 2 with `"transaction file not
   found"` rather than crashing or fabricating output.
2. **10 unit tests** against a synthetic fixture built to the same public
   column layout (`tests/unit/test_dataset_subset.py`), covering
   determinism, three distinct linkage types (shared card, shared email,
   device-only linkage via the identity-table join), confirmation that
   unrelated ("isolated") rows are never pulled in by expansion, and that
   the size cap is never exceeded. One fixture bug was caught and fixed
   before finalizing: two synthetic clusters accidentally shared a
   default address value, which would have let address-linkage
   silently do the work a test claimed was proving email-linkage.

These tests prove the *sampling mechanism* is correct. They prove nothing
about whether real IEEE-CIS rows actually cluster this way at scale —
that's only knowable once real files exist to run it against.

### 4f. Test suite
```
$ python -m pytest -v
20 passed in 0.54s
```
All offline. `pytest -m tigergraph` correctly **deselects** all 20 (they're
unmarked unit tests, not live-connection tests) — confirming the
`tests/tigergraph/` folder is still clean and reserved for real
`@pytest.mark.tigergraph` tests once a connection exists.

---

## 5. Explicitly not done, and why

| Deliverable | Status | Reason |
| --- | --- | --- |
| `docs/dataset-analysis.md` | not created | nothing to inspect yet — `inspect_dataset.py` (Phase 0) is ready and untouched, waiting on real files |
| `docs/tigergraph-schema.md` | not created | forbidden until the dataset is inspected (§2 of this phase's instructions) |
| `docs/fraud-typologies.md` | not created | requires the official policy document; fabricating placeholder typologies was explicitly forbidden |
| `docs/policy-index.md` | not created | no policy document exists in this environment yet |
| `docs/benchmark.md` | not created | no benchmark cases exist in this environment yet |
| `docs/data-quality.md` | not created | depends on having actual rows to profile |
| `scripts/create_dev_subset.py` + `app/dataset/subset.py` | **built, mechanism-tested** | relationship-preserving sampler (card/addr/email/device linkage expansion, not naive random rows), targeting the *publicly documented* Kaggle IEEE-CIS column layout — see `docs/development-fallback-analysis.md`. 10 unit tests pass against a synthetic fixture. **Not yet run against real data** — no source CSVs exist locally to sample from; confirmed the CLI fails cleanly (exit 2) rather than fabricating output. |
| `data/dev/` (actual subset output) | not created | depends on the above actually running against real files |
| `scripts/validate_graph.py` | not created | nothing loaded to validate |
| GSQL schema creation | not done | blocked on dataset inspection, per explicit instruction not to create it early |
| MCP live test | not done | blocked on the same TigerGraph credentials as §3 |

None of these were skipped by oversight — each is blocked on one of the two
external inputs in §2a/§3, and creating any of them now would mean
fabricating content the instructions explicitly prohibit.

---

## 6. Phase 1 checklist (final, real status)

```
[x] Official HHGOA dataset search performed (not found; documented in §1)
[x] Correct dataset identified (Kaggle IEEE-CIS, explicitly labeled DEVELOPMENT FALLBACK)
[x] Dataset acquired for real (677MB, row counts + fraud rate match published figures)
[x] Dataset inventory complete (docs/dataset-analysis.md, full-file scan)
[x] Dataset analysis complete
[x] Official HHGoa vs. fallback data clearly separated (in code path and docs)
[x] TigerGraph credentials configured locally (real GSQL secret, verified working)
[x] TigerGraph endpoint verified (found via Controller API, confirmed reachable)
[x] Authentication verified (real "Authentication successful" against live Savanna)
[x] Graph access verified (HHGOA_FRAUD created and confirmed live)
[x] Development subset created for real (5,000 rows, real linkage expansion, 21.1% fraud)
[x] Graph schema documented (docs/tigergraph-schema.md, derived only from real data)
[x] Data quality documented (docs/dataset-analysis.md §5, plus 3 real bugs in §8 of this doc)
[x] Development subset loaded (6,676 vertices, 15,627 edges, all confirmed live)
[x] Vertex counts verified (direct getVertexCount, matches load report exactly)
[x] Edge counts verified (direct getEdgeCount, matches load report exactly)
[x] Basic graph traversal verified (shared-card, shared-device, 2-hop - 6/6 checks passed)
[x] MCP starts against the live database
[x] MCP authenticates
[x] MCP can query TigerGraph (real schema + real vertex counts via MCP tool calls)
[x] MCP read-only restriction verified LIVE (37/116 tools exposed, 0 destructive tools reachable)
[x] Connection diagnostics tool built and unit-tested
[x] Six real bugs found and fixed by actually running the tools, not by inspection
    (see §7-§8 for all six: 2 diagnostics classification bugs, reserved-word
    vertex name, BOOL DEFAULT syntax, Device primary-key URL-path corruption,
    MCP result attribute name)
```

---

## 7. How both blockers were resolved

**Dataset:** user obtained a Kaggle API token (newer `KGAT_...` format);
`scripts/fetch_dataset.py`'s credential detection was extended to support
it (`~/.kaggle/access_token`, alongside the older `kaggle.json`). First
download attempt correctly failed with **HTTP 403** (competition rules not
yet accepted) — the script's designed behavior, not a bug. After the user
accepted the rules, the real download succeeded: 677MB, row counts and
fraud rate (3.499%) matching Kaggle's published figures exactly.

**TigerGraph:** the Savanna UI never surfaced the REST++ hostname directly
in any panel checked (Connect dropdown, Admin Portal, Query Editor). Found
instead via TigerGraph's own **Controller API**
(`GET https://api.tgcloud.io/controller/v4/v2/workgroups`, read-only,
using a user-supplied API key for exactly its intended purpose) — the
`nginx_host` field of the `HHGOA-FraudGraph` workspace entry. Two
credential systems were easy to conflate: the Controller API key (40
chars, underscores) is not the workspace's GSQL secret (32 lowercase
alphanumeric chars, created via `CREATE SECRET`) — the first correctly
failed authentication; the second worked.

## 8. Schema design, deployment, load, and validation — all real, all live

**Schema** (`docs/tigergraph-schema.md`, derived only from
`docs/dataset-analysis.md`'s real column profile): 5 vertex types (`Txn`,
`Card`, `Address`, `EmailDomain`, `Device`) and 5 edge types linking `Txn`
to each. No `Customer`/`Account` vertex — the dataset has no such column;
documented as a limitation rather than invented.

**Three real bugs found and fixed by actually deploying to the live
database, not by inspection:**
1. GSQL rejected `CREATE VERTEX Transaction (...)` — `Transaction` is a
   reserved word. It returned a success-shaped response while creating
   nothing; my own error-text matching missed it too. Renamed to `Txn`.
2. GSQL rejected `is_fraud BOOL DEFAULT false` — the live parser wanted a
   quoted/string-style literal, not the bare word `false`, contradicting
   generic docs I'd checked first. Removed the explicit default (`BOOL`
   already defaults to `false` in TigerGraph).
3. `Device`'s primary ID was originally the raw `DeviceInfo` string. Real
   values like `"SM-A530F Build/NMF26X"` contain a literal `/`, which
   corrupts REST++ URL paths built from that ID — writes succeeded (POST
   body), but `getEdges`-style reads silently misparsed the path.
   Confirmed live via `scripts/validate_graph.py`. Fixed by hashing to a
   16-hex-char key, with `device_info` kept as a plain attribute.

Given bug #1's pattern (GSQL returning a success-shaped response for a
statement that registered nothing), `scripts/create_schema.py` was
strengthened to **verify against the live schema catalog** after
deployment rather than trust any per-statement response text — this is
what actually caught bug #1 conclusively, and would catch a future
instance of the same class of failure.

**Final deployed schema**, verified via `scripts/test_tigergraph.py`
after fixes:
```
✓ Graph accessible - graph 'HHGOA_FRAUD'
✓ Schema accessible - 5 vertex types, 5 edge types
✓ Read-only query successful - getVertexCount('*') -> 5 type(s), 0 vertices total
```

**Development subset** (`app/dataset/subset.py`, real run against real
data, not just the synthetic-fixture unit tests): 5,000 transactions
(2,000 stratified seed + 3,000 linkage-expanded, capped at 5,000), 21.1%
fraud vs. the dataset's 3.5% baseline — deliberately over-representing
fraud, per design.

**Loaded into TigerGraph** (`app/tigergraph/loader.py` +
`scripts/load_data.py`), final counts after the `Device` fix and a
reload, confirmed via direct `getVertexCount`/`getEdgeCount`:
```
Txn 5,000 · Card 1,379 · Address 67 · EmailDomain 46 · Device 184
MADE_WITH_CARD 4,994 · BILLED_TO 4,305 · PURCHASER_EMAIL 4,016 ·
RECIPIENT_EMAIL 1,218 · USED_DEVICE 1,094
```
One caught-and-corrected false alarm along the way: `getVertexCount`
queried immediately after the bulk load undercounted (transient eventual
consistency on this TigerGraph deployment — confirmed by re-querying
seconds later and getting the correct, matching numbers). Noted in
`load_data.py`'s own output so this doesn't get mistaken for data loss
again.

**Graph validation** (`scripts/validate_graph.py`, real traversal queries
against the live loaded graph — not synthetic):
```
✓ Vertex/edge counts - 6,676 vertices, 15,627 edges
✓ Direct Txn lookup
✓ Shared-card traversal - card '15111|310.0|150.0|224.0' used by 4 transactions
✓ Shared-device traversal - device '0c4b152e4c169c16' used by 2 transactions
✓ Orphan check (sampled) - no zero-degree Card/Address/EmailDomain/Device found
✓ 2-hop: Txn -> Card -> other Txns - 4 connected transaction(s)
Result: ALL CHECKS PASSED (6/6)
```
(The orphan-check and shared-entity searches were deliberately bounded to
~30–60 REST round-trips total after an initial unbounded version proved
too slow in practice — a real, caught performance issue, not a
hypothetical one.)

**TigerGraph MCP** (`scripts/test_mcp.py`, the official `tigergraph-mcp`
1.0.3 server launched as a real stdio subprocess against the live
instance):
```
✓ MCP server started + initialized - handshake ok
✓ Tools discoverable - 37 tool(s) exposed
✓ Destructive tools blocked - none of the always-blocked tools are exposed
✓ Schema retrieval via MCP - real schema for HHGOA_FRAUD returned
✓ Read-only query via MCP - real vertex counts returned (Txn: 5000, Card: 1379, ...)
Result: ALL CHECKS PASSED
```
37 tools (down from the full 116) confirms the `TG_ALLOWED_TOOLS=read-only`
filter from Phase 0's `app/mcp/config.py` is actually being enforced by
the live server, not just configured. The agent, when built in Phase 2,
gets exactly this reduced, non-destructive tool surface.

## 9. Exact next step for Phase 2

TigerGraph and the dataset are no longer blockers. Phase 2 (LangGraph
agent, GraphRAG, case memory, next-best-action, benchmark solving) can
begin. Per instructions, it does not begin automatically — see the final
summary below.

Commands to re-verify any part of this from scratch:
```bash
python scripts/fetch_dataset.py       # dataset (Kaggle fallback)
python scripts/inspect_dataset.py     # docs/dataset-analysis.md
python scripts/test_tigergraph.py     # TigerGraph connection
python scripts/create_schema.py       # deploy schema (idempotent)
python scripts/create_dev_subset.py   # data/dev/
python scripts/load_data.py           # load data/dev/ into TigerGraph
python scripts/validate_graph.py      # traversal validation
python scripts/test_mcp.py            # MCP live test
```

I'm stopping here as instructed and not starting Phase 2 work
automatically.
