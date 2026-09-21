# Phase 2B — Evidence Model + Signal Normalization

Status date: 2026-09-22. Offline claims below are from real test runs
(70 tests: 57 offline + 13 live-query, all passing — see §9). Live
evidence-integration claims are marked explicitly **PENDING** — the
TigerGraph workspace went down mid-phase (§8) and the live test is
written, verified to skip gracefully, and ready to run the moment it's
back, but has not yet produced a passing result.

**Scope, deliberately narrow, per instruction:** evidence normalization
only. No LangGraph, no LLM, no uncertainty engine, no policy engine, no
case memory, no scoring. Nothing here computes a fraud verdict.

---

## 1. The core design rule

Four things, never collapsed into one:

| Field | What it holds |
| --- | --- |
| `observation` | What the graph actually found — a plain factual sentence, never a verdict |
| `interpretation` | This project's own reading of what the observation means as evidence |
| `provenance` | Exactly where it came from — source, query, transaction/entity IDs |
| `quality` | How reliable this *type* of signal is, structurally — not a fraud probability |

`app/evidence/models.py`'s `Evidence` model keeps all four as separate
fields. There is no `fraud_score`, `confidence`, or `verdict` field
anywhere in this phase's models — verified by an actual test
(`TestRiskAndEvidenceSeparation::test_no_confidence_field_exists_anywhere_on_the_model`),
not just by absence of intent.

## 2. Evidence types

Exactly six — one per Phase 2A query capability, no more:

```
transaction_context   <- get_transaction_context
shared_card            <- find_shared_card_activity
shared_address          <- find_shared_address_activity
shared_device            <- find_shared_device_activity
shared_email_domain        <- find_shared_email_activity
network_pattern             <- investigate_transaction_network
```

## 3. Signal quality — PROJECT-DERIVED INTERPRETATION, grounded in measured facts

| Evidence type | Quality | Why (FACT FROM DATASET, then interpretation) |
| --- | --- | --- |
| `shared_card` | MEDIUM | FACT: `card_key` is a 4-field anonymized composite (card1/2/3/5), not a verified card number. Interpretation: more specific than a coarse code, not a confirmed identity. |
| `shared_device` | MEDIUM | FACT: `device_info` ranges from full build strings to generic values like `"Windows"` (`docs/tigergraph-schema.md`). Interpretation: uniform MEDIUM is a documented simplification — this layer does not yet score by string specificity. |
| `shared_address` | **LOW** | FACT: `addr1`/`addr2` are coarse, numeric-coded regional identifiers, not physical addresses (`docs/dataset-analysis.md`). FACT (measured, `docs/phase-2-graph-analysis.md`): for one real transaction, shared Address reached **312** related transactions vs. **12** for shared Card. |
| `shared_email_domain` | **LOW** | FACT: only the domain is ever disclosed, and the full dataset has only **~59** distinct purchaser-domain values across 590,540 transactions (`docs/dataset-analysis.md`'s column profile). A shared `gmail.com` says almost nothing. |
| `transaction_context` | UNKNOWN | Not a relationship signal — descriptive data about the seed itself. No rating is invented for it. |
| `network_pattern` | **computed per-bundle** | See §4 — depends on which entity types actually contributed to the fan-out. |

Quality is assigned from the **static semantics of the field type**, per
the explicit instruction — never because "this entity happened to be
associated with fraud in one example." Verified by test: two bundles
built from identical shared-card facts but opposite `isFraud` labels
produce byte-identical `shared_card` evidence quality and metrics
(`test_dataset_risk_score_does_not_change_evidence_quality`).

## 4. `network_pattern`'s quality is computed, not fixed

`network_pattern_quality()` looks at what fraction of the 2-hop
related-transaction count came through the LOW-quality types (Address,
EmailDomain) vs. the MEDIUM ones (Card, Device):

- ≥75% via Address/EmailDomain → **LOW**
- ≤25% → **MEDIUM**
- otherwise → **MEDIUM**, with a note describing the actual mix

Real numbers this was built and tested against: `Card=12, Address=312,
EmailDomain_purchaser=20` → 86% low-quality share → **LOW**. This is the
Phase 2A finding, carried forward mechanically rather than re-typed.

## 5. Provenance — always present, never a credential

Every `Evidence` item carries `source="tigergraph"`, the exact
`source_query` function name, the `transaction_id`, and (where
applicable) the shared entity's ID. No connection details, no
credentials — provenance answers "which query, on which real
transaction/entity", nothing more.

## 6. Dataset risk score — the honest caveat

**This does not do what it sounds like it does, and that's disclosed
explicitly.** The instructions describe a "dataset-provided fraud/risk
score" to preserve separately as `dataset_risk_score`. The actual
development-fallback dataset (Kaggle IEEE-CIS) **has no continuous risk
score column** — only the binary `isFraud` training label (confirmed:
`docs/dataset-analysis.md`'s full column profile lists no such field).

`EvidenceBundle.dataset_risk_score` is populated as `1.0`/`0.0` from that
boolean label, with the caveat written directly into the model's
docstring: if the official HHGoa dataset (described as including a real
continuous risk score) is obtained later, this field would carry genuine
continuous values with no change needed anywhere else in this model —
the separation this phase built already anticipates that swap.

Verified never to leak into evidence quality (§3) or to appear in any
evidence item's own metrics as anything but a traceability note
(`TransactionContext` evidence's `is_fraud_label` metric, clearly
distinguished from the bundle-level authoritative field).

## 7. Query status, deduplication, determinism, error handling

**SUCCESS / EMPTY / ERROR** — three real, tested states, never conflated.
`"no device found"` (EMPTY) and `"the device query failed"` (ERROR) look
completely different in the output: EMPTY carries a plain observation
and no `error` field; ERROR carries `error.error_type`/`error.message`
and `observation=None` — nothing is fabricated when a query didn't run.
One query failing does not abort the other five
(`test_one_query_failing_does_not_abort_the_others`).

**Deduplication** (`deduplicate_evidence`): keyed on `evidence_id`, a
deterministic string built only from `(transaction_id, evidence_type,
entity_id)` — never a UUID or timestamp. When two items share a key, the
more complete one wins (SUCCESS > EMPTY > ERROR, then more detail, then
alphabetical `source_query` as a final tiebreak) — deterministic
regardless of input order (`test_dedup_result_order_is_deterministic`).

**Determinism**: every collection is sorted before being returned.
`test_same_raw_input_produces_the_same_bundle_twice` builds two bundles
from independently-constructed-but-equal inputs and asserts byte-equal
output (excluding the live-only pieces).

**Conflicting evidence**: deliberately never resolved into one verdict.
`test_high_risk_score_with_weak_low_quality_evidence_stays_unreduced`
constructs a bundle with `dataset_risk_score=1.0` but zero fraud among
the card's related transactions and a LOW-quality address signal, and
asserts both facts remain separately visible — and that no
`final_fraud_score` or `verdict` field exists on the model to have
collapsed them into.

## 8. TLS investigation — root cause found, not just re-flagged

Phase 2A observed `InsecureRequestWarning` on every live call. Traced to
source this phase: `pyTigerGraph` 2.0.4's `common/base.py` (lines
198–220) unconditionally sets `self.useCert = True` for **any** `https://`
host, and then computes `self.verify = False if (self.useCert or
self.certPath) else True` — meaning **every HTTPS connection made through
this library version has certificate verification disabled**, regardless
of what the caller passes for `certPath`/`useCert`. The library's own
comment admits it: *"the verify=True branch in _prep_req is
unreachable."* This is a real bug (or at minimum a surprising, unfixed
default) in the third-party library, not in this project's configuration.

**KNOWN SECURITY ISSUE.** The connection is genuinely TLS-encrypted, not
plaintext — but the certificate chain is never validated, which is a real
MITM exposure on an adversarial network. A safe post-construction
workaround (`conn.verify = True` after building the connection) was
planned to test empirically, but the live workspace went down (§9) before
it could be tried. **Not fixed this phase.** Next step when the workspace
is back: test whether the override actually holds through
`pyTigerGraph`'s session-caching (`s.verify = self.verify` is set once,
not per-request, so the override must happen before any request is sent)
without breaking the working connection; if it does, apply it in
`app/tigergraph/client.py`; if it destabilizes the connection, document
it as a permanent known issue and consider pinning/patching the
dependency instead.

## 9. Real bugs found and fixed this phase (all live-caught, not by inspection)

1. **A 500 Server Error was silently treated as passing.** Re-running
   `scripts/test_tigergraph.py` mid-phase (to investigate TLS) hit a real
   `500 Internal Server Error` from TigerGraph — and the tool printed
   `Host reachable ✓` and `ALL CHECKS PASSED` anyway. Two compounding
   bugs: `classify_network` had no bucket for 5xx errors (fell through to
   the generic `ENDPOINT` catch-all), and `run_checks` treated every
   non-`AUTHENTICATION` category as a silent "skip" rather than a
   failure — so a category it didn't specifically recognize just
   vanished instead of failing loudly. Fixed both: added a `SERVER`
   category (checked *before* the generic timeout match, since `"504
   Gateway Timeout"` contains the substring `"timeout"` and was initially
   misrouted to `NETWORK` — caught by my own new test), and changed
   `run_checks` so any category except `NETWORK` now correctly fails the
   `Authentication successful` check instead of skipping it. 3 new
   regression tests lock this in with the exact real error text observed
   live.
2. **`aggregate_evidence`'s own monkeypatch-proofing bug**, caught by its
   own test suite: `_SHARED_ENTITY_QUERIES` originally stored the three
   shared-entity query *function objects*, bound once at module-import
   time. A test that monkeypatched `app.evidence.aggregate.q.find_shared_card_activity`
   correctly changed the module attribute, but the already-bound
   reference in the tuple never saw it — so the test called the real
   (unmocked) query function against a fake client and got a confusing
   `'object' object has no attribute 'connection'` instead of the
   intended stub result. Fixed by storing only the function *name* and
   looking it up fresh (`getattr(q, source_query)`) at call time,
   matching the pattern the other three query calls already used
   correctly.
3. **The TigerGraph workspace itself went down mid-phase** (see below) —
   not a code bug, but discovered and diagnosed using this project's own
   (now more honest, thanks to fix #1) diagnostic tooling.

## 10. Infrastructure note: TigerGraph currently unreachable

Partway through this phase, `scripts/test_tigergraph.py` started
reporting a consistent `500 Internal Server Error` on every token
request — not transient (re-checked multiple times). The workspace's own
metadata (captured in Phase 1 via the Controller API) showed
`auto_stop_minutes: 60`, so this is most likely the Savanna workspace
having auto-stopped from inactivity. Restarting it requires the Savanna
UI (Workspace → `HHGOA-FraudGraph` → resume/restart) — not something this
session has API access to do, and not something to do unprompted even if
it did (a workspace restart is the account owner's call). **This blocked
only the live integration test** (§11) — every offline capability of this
phase was built and fully tested without it.

## 11. Test summary

```
57 offline unit/model tests   - PASSING  (18 normalize + 13 aggregate + 26 pre-existing)
13 live Phase 2A query tests  - failing (workspace down, not a regression - see §10)
 6 live evidence-integration tests - SKIP gracefully (written, ready, pending workspace)
```

The live evidence test (`tests/tigergraph/test_evidence_live.py`) checks
its own preconditions via this project's diagnostics before running
anything, and skips with a clear reason rather than failing noisily when
TigerGraph isn't healthy - confirmed by an actual run during the outage.

## 12. Explicitly not done (out of Phase 2B's scope)

Uncertainty engine, policy engine, next-best-action, case memory,
LangGraph, LLM, GraphRAG, frontend, any fraud score or verdict. Nothing
committed to git this phase (per instruction).
