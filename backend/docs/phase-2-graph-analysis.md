# Phase 2A — Graph Investigation Queries

Status date: 2026-09-21. Every number and behavior below is from an actual
test run against the live `HHGOA_FRAUD` graph (`tests/tigergraph/test_investigation_queries.py`,
13/13 passing) — nothing here is inferred.

**Scope, deliberately narrow:** this covers only Phase 2A — the graph
query layer. No evidence-strength scoring, no uncertainty model, no
LangGraph, no agent. Those are later phases and are not started here.

---

## 1. What already existed vs. what this phase built

Inspected before writing anything: `app/tigergraph/{client.py, schema.py,
loader.py, diagnostics.py, health.py}`, `app/agent/` (empty stubs only:
`__init__.py` files, no logic), `tests/tigergraph/` (empty, reserved).
`app/tigergraph/queries.py` and `algorithms.py` were named in the Phase 0
plan but never created — clean slate, no duplication risk.

Built this phase: `app/tigergraph/queries.py` (6 query functions) and
`tests/tigergraph/test_investigation_queries.py` (13 live tests).

## 2. Live schema, re-verified before designing queries

Per instruction, the live schema was re-fetched rather than trusted from
memory:

```
VERTEX Txn         - PK: transaction_id  (46 attributes - see docs/tigergraph-schema.md)
VERTEX Card        - PK: card_key         (card1, card2, card3, card5, card_network, card_type)
VERTEX Address     - PK: address_key      (addr1, addr2)
VERTEX EmailDomain - PK: domain           (no other attributes)
VERTEX Device      - PK: device_key       (device_info, device_type)

EDGE MADE_WITH_CARD    Txn <-> Card         undirected
EDGE BILLED_TO         Txn <-> Address      undirected
EDGE PURCHASER_EMAIL   Txn <-> EmailDomain  undirected
EDGE RECIPIENT_EMAIL   Txn <-> EmailDomain  undirected
EDGE USED_DEVICE       Txn <-> Device       undirected
```

This confirms there is no `Customer`/`Account` vertex to query against —
consistent with `docs/tigergraph-schema.md` §0. Every query below reaches
only entities that actually exist.

## 3. Design decisions

**Interpreted, not installed, queries.** `CREATE QUERY` + `INSTALL QUERY`
took ~30–60s per query against this Savanna instance during Phase 1's
schema deployment — too slow to iterate on six queries while proving them
out. `runInterpretedQuery` executes immediately, at some per-call latency
cost (observed: 0.3–2.6s per call in testing). Revisit compiling the
hot-path queries once Phase 2's actual tool-call volume is known —
noted as a follow-up, not silently decided.

**Typed `VERTEX<Txn>` parameters, plain-string IDs.** Confirmed live: for
a *typed* vertex parameter, `pyTigerGraph.runInterpretedQuery` wants the
bare primary-ID string, not the `(id, "type")` tuple the same library
wants for an *untyped* `VERTEX` parameter. Got this wrong on the first
attempt (`'Failed to convert Txn vertex id for parameter exclude'`),
confirmed the correct form from the live docstring, fixed, re-tested.

**Client-side field trimming.** A raw `PRINT` of a `Txn` vertex set
returns all 46 attributes (mostly opaque `C*`/`D*`/`M*` columns — see
`docs/tigergraph-schema.md` §1.1). Related-transaction results are
trimmed in Python to five fields (`transaction_id`, `is_fraud`,
`transaction_amt`, `transaction_dt`, `product_cd`) rather than adding
GSQL attribute-projection syntax on top of everything else already being
proven live in one pass.

**No evidence scoring in this layer.** Every function returns facts
(counts, IDs, attributes) with no confidence/strength values attached.
That's Phase 2B's explicit job, not this one's.

## 4. The six queries

| Function | Question it answers | Real test result (txn `2987937`) |
| --- | --- | --- |
| `get_transaction_context(txn_id)` | This transaction's own data, and what's directly linked to it (1 hop) | card `18227\|583.0\|150.0\|226.0`, address `299.0\|87.0`, purchaser domain `sbcglobal.net`, no device |
| `find_shared_card_activity(txn_id)` | What else used this same card? | 12 other transactions |
| `find_shared_address_activity(txn_id)` | What else billed to this same address? | 312 other transactions |
| `find_shared_device_activity(txn_id)` | What else used this same device? | 0 (no device on this txn — not an error) |
| `find_shared_email_activity(txn_id)` | What else shares this purchaser or recipient email domain? | 20 via purchaser domain, 0 via recipient (none present) |
| `investigate_transaction_network(txn_id)` | 2-hop fan-out, broken down by which entity type formed each connection | `{Card: 12, Address: 312, EmailDomain_purchaser: 20, EmailDomain_recipient: 0, Device: 0}` |

All numbers above are asserted exactly in
`tests/tigergraph/test_investigation_queries.py` — including
cross-checking that `investigate_transaction_network`'s breakdown agrees
with the four dedicated shared-entity queries run independently
(`TestNetworkSummary::test_breakdown_matches_the_individual_queries_exactly`),
which is a genuine consistency check between two separate GSQL query
implementations, not a tautology.

## 5. Real finding: Address is a much noisier signal than Card

For the same seed transaction: shared **Card** → 12 related transactions,
shared purchaser **EmailDomain** → 20, shared **Address** → **312**.

This isn't a coincidence of one example — `addr1`/`addr2` are documented
in `docs/dataset-analysis.md` as coarse, numeric-coded *region* codes, not
a precise billing address. Many genuinely unrelated transactions
legitimately share one. **This has a direct consequence for Phase 2B's
evidence model**: "shared entity" cannot be treated as one undifferentiated
signal type. A future confidence/strength calculation must weight
`Address` matches far lower than `Card` or `Device` matches, or an
investigation will drown in false-positive "connections" every time two
transactions happen to come from the same metro area.

`investigate_transaction_network`'s per-entity-type breakdown (rather than
a single combined fan-out count) exists specifically so this distinction
is preserved for whatever consumes it next, instead of being averaged away.

## 6. Graph algorithms — deliberately not addressed this phase

The broader Phase 2 plan lists graph algorithms (PageRank, connected
components, community detection, similarity) as a later consideration.
Per this phase's explicit scope ("Start with PHASE 2A only... Do NOT
start LangGraph yet... Stop and report"), no algorithm work was done or
decided here. Whether any of these are worth adding is a decision for
whoever reviews this report, informed by §5's finding — e.g., a coarse
`Address` signal is exactly the kind of case where a real algorithm
(a weighted-degree or community-detection pass) might do a better job
than a raw shared-neighbor count, but that evaluation hasn't happened yet.

## 7. Known issues

- **`InsecureRequestWarning` observed during live test runs**:
  `urllib3` reports the HTTPS connection to the Savanna endpoint isn't
  verifying the TLS certificate chain. The connection is still encrypted;
  certificate validation is disabled somewhere in the current
  `pyTigerGraph`/connection configuration. Not investigated or fixed this
  phase — flagged here rather than silently ignored, since it's a real
  security-relevant observation from an actual test run, not a
  hypothetical concern.
- **Interpreted-query latency**: 0.3–2.6s per call observed. Fine for six
  queries in a test suite; worth revisiting (installed queries, or
  batching) before this becomes the hot path for an agent making many
  calls per investigation.
- **`investigate_transaction_network`'s `total_related` is not
  deduplicated** — documented directly on the dataclass property: the
  same related transaction can be reached via more than one entity type
  (e.g., shares both the card and the address), and the sum does not
  collapse that. Treat it as an upper bound, not an exact distinct count.

## 8. What was explicitly not done (out of Phase 2A's scope)

Evidence/confidence model, uncertainty engine, policy engine, case model,
LangGraph workflow, FastAPI endpoints, GraphRAG, frontend — none started.
Per the stop condition given for this phase, work halts here for review
before any LLM sits on top of these queries.
