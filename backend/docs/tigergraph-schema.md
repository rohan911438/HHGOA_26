# TigerGraph Schema — HHGOA_FRAUD

**DEVELOPMENT FALLBACK SCHEMA — designed from IEEE-CIS (Kaggle), not the
official HHGoa dataset.** Every vertex, edge, and attribute below is
derived from a real column in `docs/dataset-analysis.md` (full-file scan,
2026-09-21) — nothing here is invented. Where the data does not support an
entity the original architecture speculated about, that is stated
explicitly rather than filled in with a guess.

Not yet created in TigerGraph. This is the design; `scripts/create_schema.py`
deploys it once reviewed.

---

## 0. The most important limitation

**This dataset has no customer or account identifier.** No `customer_id`,
`account_id`, or equivalent column exists in `train_transaction.csv` or
`train_identity.csv` (confirmed: grepped `docs/dataset-analysis.md` for
"customer"/"account" — zero matches). The original architecture's
`Customer` and `Account` vertex types are **not included** in this schema
for that reason.

What the data *does* give us is four independent linking signals — card
fingerprint, billing address, email domain, device — that let the graph
surface "these transactions are probably connected" without ever claiming
they belong to one ground-truth customer. That distinction matters for
investigation semantics: a shared-card cluster is evidence a human or
policy should weigh, not a resolved identity. If the official HHGoa
dataset provides real customer/account IDs, `Customer`/`Account` vertices
and an `OWNS`-style edge should be added straightforwardly on top of this
schema — nothing here conflicts with that.

---

## 1. Vertex types

### 1.1 `Transaction`

The core entity. One vertex per row of `train_transaction.csv`.

| Attribute | Type | Source column | Notes |
| --- | --- | --- | --- |
| `transaction_id` (PK) | `STRING` | `TransactionID` | True primary key — see `docs/dataset-analysis.md`'s correction note on why the automated report didn't flag it as one |
| `is_fraud` | `BOOL` | `isFraud` | **Training-only label.** 3.499% positive (20,663 / 590,540, full-file count). A real, new transaction under investigation would not have this — it exists here because IEEE-CIS is a labelled training set, not because a live system would know it in advance |
| `transaction_dt` | `INT` | `TransactionDT` | Seconds offset from an undisclosed reference point, **not** a calendar timestamp. Valid for ordering and computing deltas between transactions; not valid for "what date was this" |
| `transaction_amt` | `DOUBLE` | `TransactionAmt` | 0% null |
| `product_cd` | `STRING` | `ProductCD` | 5 categorical values (W/H/C/S/R), 0% null |
| `dist1` | `DOUBLE`, nullable | `dist1` | 65.82% null. Kept as an attribute, not a structural element — too sparse to justify its own vertex/edge |
| `dist2` | `DOUBLE`, nullable | `dist2` | 92.24% null |
| `c1`–`c14` | `DOUBLE` | `C1`–`C14` | 0% null throughout. Vesta-disclosed as "counting features"; exact meaning undisclosed. Kept as opaque numeric attributes — useful as future risk-scoring input, not as graph structure, since nothing about them is known to represent an identity or relationship |
| `d1`–`d15` | `DOUBLE`, nullable | `D1`–`D15` | "Timedelta" features, meaning of *between what* undisclosed. Null rates 0%–93.6% (see `docs/dataset-analysis.md` §5) |
| `m1`–`m9` | `BOOL`/`STRING`, nullable | `M1`–`M9` | "Match" flags, meaning of *match between what* undisclosed. Null rates 39%–81% |

**Deliberately excluded: `V1`–`V339`.** 339 Vesta-engineered columns,
individually opaque (no public semantic mapping), many 50–95% null. Adding
them would make `Transaction` a 350+-attribute vertex that is mostly
unusable noise for graph reasoning. The raw CSV remains the source of
truth; if a specific investigation query later needs one, add it by name
with a documented reason, not as a bulk import. See
`docs/dataset-analysis.md` §7 for the full reasoning.

### 1.2 `Card`

Represents a **card fingerprint**, not a verified card number — IEEE-CIS
never discloses real card numbers.

| Attribute | Type | Source column(s) | Notes |
| --- | --- | --- | --- |
| `card_key` (PK) | `STRING` | composite of `card1`,`card2`,`card3`,`card5` | Synthetic key, format `"{card1}|{card2}|{card3}|{card5}"` — same construction already used and unit-tested in `app/dataset/subset.py`'s linking logic |
| `card1` | `STRING` | `card1` | 0% null, numeric-coded |
| `card2` | `STRING`, nullable | `card2` | 1.51% null |
| `card3` | `STRING` | `card3` | 0% null |
| `card5` | `STRING`, nullable | `card5` | 0.53% null |
| `card_network` | `STRING` | `card4` | visa / mastercard / discover / amex |
| `card_type` | `STRING` | `card6` | credit / debit |

### 1.3 `Address`

| Attribute | Type | Source column(s) | Notes |
| --- | --- | --- | --- |
| `address_key` (PK) | `STRING` | composite of `addr1`,`addr2` | Format `"{addr1}|{addr2}"` |
| `addr1` | `STRING` | `addr1` | 10.7% null. Numeric-coded region; exact geography undisclosed |
| `addr2` | `STRING` | `addr2` | 10.7% null |

### 1.4 `EmailDomain`

| Attribute | Type | Source column | Notes |
| --- | --- | --- | --- |
| `domain` (PK) | `STRING` | `P_emaildomain` or `R_emaildomain` | The domain only (e.g. `gmail.com`) — IEEE-CIS never discloses full email addresses. One vertex is shared by every transaction using that domain; this is a coarse signal (many unrelated people share `gmail.com`), useful mainly in combination with other shared entities, not alone |

### 1.5 `Device`

| Attribute | Type | Source column | Notes |
| --- | --- | --- | --- |
| `device_key` (PK) | `STRING` | derived: `sha256(DeviceInfo)[:16]` | **Not** the raw `DeviceInfo` string — see the correction below |
| `device_info` | `STRING` | `DeviceInfo` (from `train_identity.csv`) | Full original value, verbatim. 17.73% null within the identity file; and only ~24.4% of transactions (144,233 / 590,540) have a matching identity row at all — most transactions will have **no** `Device` vertex |
| `device_type` | `STRING` | `DeviceType` | mobile / desktop |

**Correction found while loading real data:** the first version of this
schema used `device_info` (the raw string) directly as the primary ID —
the same pattern that works fine for `EmailDomain.domain`. It broke on
real data: `DeviceInfo` values are uncontrolled vendor strings, and some
contain a literal `/` (e.g. `"SM-A530F Build/NMF26X"`). A `/` inside a
TigerGraph primary ID corrupts the REST++ URL path built from that ID —
writes via `upsertVertices` (POST body) happened not to hit this and
loaded without error, but reads that build a URL path from the vertex ID
(`getEdges`, confirmed live: `scripts/validate_graph.py`'s shared-device
check) silently misparsed the path and returned a confusing "USED_DEVICE
is not a valid vertex type" error — the edge-type argument had been
shifted into the wrong URL segment by the extra `/`. Fixed by hashing to
a primary key guaranteed URL-safe, with the original string kept as a
plain attribute so nothing is lost. `app/tigergraph/loader.py`'s
`_device_key()` has the full explanation inline.

**Deliberately excluded: most `id_01`–`id_38`.** Opaque, mixed
numeric/categorical, many 44–97% null, no public semantic mapping. Two
observations from the raw values were self-evident enough to note but are
**not** added as attributes in this v1 schema (kept as an open item for
schema v2 if an investigation query specifically needs them):
`id_30`/`id_31`/`id_33` look like OS/browser/screen-resolution device
fingerprint strings (e.g. `"Android 7.0"`, `"chrome 62.0"`, `"2220x1080"`),
and `id_23` contains literal `"IP_PROXY:TRANSPARENT"` /
`"IP_PROXY:ANONYMOUS"` values (96.42% null — only populated for a specific
subset of sessions). These readings come directly from the example values
already captured in `docs/dataset-analysis.md`, not from external
documentation Vesta never published.

---

## 2. Edge types

All edges connect `Transaction` to one of the four linking-entity vertex
types above. Declared `REVERSE_EDGE`-enabled so "find everything sharing
this card/address/email/device" is a single reverse hop, which is the
core operation the investigation queries in the project's phase plan
(`find_shared_devices`, `find_shared_connections`, `find_related_accounts`)
depend on.

| Edge | From → To | Source | Cardinality note |
| --- | --- | --- | --- |
| `MADE_WITH_CARD` | `Transaction` → `Card` | `card1,card2,card3,card5` | Every transaction has exactly one |
| `BILLED_TO` | `Transaction` → `Address` | `addr1,addr2` | 10.7% of transactions have neither `addr1` nor `addr2` — no edge created for those |
| `PURCHASER_EMAIL` | `Transaction` → `EmailDomain` | `P_emaildomain` | 16.08% null — edge omitted when null |
| `RECIPIENT_EMAIL` | `Transaction` → `EmailDomain` | `R_emaildomain` | 68.18% null — edge omitted when null. Same `EmailDomain` vertex type as `PURCHASER_EMAIL`; a domain can be reached via either edge type |
| `USED_DEVICE` | `Transaction` → `Device` | `DeviceInfo` (via identity-file join on `TransactionID`) | Only ~24.4% of transactions have a matching identity row, and 17.73% of *those* have a null `DeviceInfo` — most transactions have no `Device` edge at all |

No edge type currently carries its own attributes; each is a pure
structural link. (`DeviceType` is stored on the `Device` vertex rather
than the edge — a simplification: if the rare case of the same
`DeviceInfo` string appearing with different `DeviceType` values across
transactions turns out to matter, move it to the edge instead.)

---

## 3. Primary IDs and indexes

| Vertex | Primary ID | Rationale |
| --- | --- | --- |
| `Transaction` | `transaction_id` | Documented unique key (Kaggle data dictionary); confirmed as the join key between both source files |
| `Card` | `card_key` (composite) | No real card number exists in the source; the 4-column fingerprint is the finest-grained card-identifying signal available |
| `Address` | `address_key` (composite) | Same reasoning — `addr1`/`addr2` alone are the only address signal |
| `EmailDomain` | `domain` | Already a natural, meaningful string key — no synthetic key needed |
| `Device` | `device_key` (hashed) | Hash of `device_info`, not the raw string — see §1.5's correction. Still conflates all transactions sharing an identical device-description string, which is intentional (that's the shared-device fraud signal) but coarse for generic strings like `"Windows"` |

**Recommended secondary indexes** (attribute indexes, not primary):
- `Transaction.is_fraud` — every investigation and every benchmark query
  filters or joins on this
- `Transaction.transaction_dt` — time-range and chronological-ordering
  queries (e.g. "prior fraud in the last N transactions on this card")

---

## 4. Column → graph mapping (full reference)

```
TransactionID          -> Transaction.transaction_id   (PK)
isFraud                -> Transaction.is_fraud
TransactionDT           -> Transaction.transaction_dt
TransactionAmt           -> Transaction.transaction_amt
ProductCD                -> Transaction.product_cd
dist1, dist2               -> Transaction.dist1, dist2
C1..C14                    -> Transaction.c1..c14
D1..D15                    -> Transaction.d1..d15
M1..M9                     -> Transaction.m1..m9
V1..V339                   -> NOT LOADED (see §1.1)

card1, card2, card3, card5 -> Card.card_key (composite PK) + individual attrs
                            -> Transaction -MADE_WITH_CARD-> Card
card4                      -> Card.card_network
card6                      -> Card.card_type

addr1, addr2                -> Address.address_key (composite PK) + attrs
                            -> Transaction -BILLED_TO-> Address

P_emaildomain                -> EmailDomain.domain (PK)
                            -> Transaction -PURCHASER_EMAIL-> EmailDomain
R_emaildomain                -> EmailDomain.domain (PK, same vertex type)
                            -> Transaction -RECIPIENT_EMAIL-> EmailDomain

DeviceInfo (identity file)     -> Device.device_key (PK, hashed) + Device.device_info (verbatim)
                            -> Transaction -USED_DEVICE-> Device
DeviceType (identity file)     -> Device.device_type
id_01..id_38 (identity file)   -> NOT LOADED (see §1.5)
```

---

## 4b. Null-handling convention

TigerGraph attributes don't have SQL-style `NULL` — a loading job needs a
concrete default for a blank CSV field. Rather than leave this implicit:

- Nullable **numeric** attributes (`dist1`, `dist2`, `card2`, `card5`,
  `d1`–`d15`) default to `-1` on a blank source value. `-1` is never a
  real value for any of these fields (all are non-negative in the source
  data), so it is an unambiguous "missing" sentinel, not a value that
  could be confused with a real 0.
- Nullable **categorical/string** attributes (`m4`, `addr1`, `addr2`)
  default to empty string `""` on a blank source value.
- `m1`,`m2`,`m3`,`m5`–`m9` are stored as `STRING` (not `BOOL`), holding
  the literal `"T"` / `"F"` / `""` — TigerGraph `BOOL` has no clean way to
  represent "missing" distinctly from `false`, and collapsing a 39–81%
  null rate into `false` would silently fabricate a "no match" signal
  where the source actually says "unknown." A consuming query that wants
  a boolean can compare the string directly.
- Missing **edges** (e.g. no `Address` because both `addr1`/`addr2` were
  blank) are simply not created, rather than pointing at a sentinel
  vertex — "no address on file" and "address is the empty string" are
  different facts and should stay different.

## 5. Why this shape

The point of putting this in a graph at all is the shared-entity
traversal: `Transaction -MADE_WITH_CARD-> Card <-MADE_WITH_CARD- Transaction`
(and the same pattern for `Address`, `EmailDomain`, `Device`) turns "does
this transaction connect to any other transaction" from an expensive
self-join over 590K rows into a two-hop graph traversal — exactly the
queries the project's phase plan names: `find_shared_devices`,
`find_shared_connections`, `find_related_accounts`,
`investigate_entity_network`. Four independent linking dimensions (not
one merged "identity" vertex) is deliberate: a shared card and a shared
device are different strengths of evidence, and collapsing them would
lose that distinction before the agent ever gets to reason about it.

Everything excluded (`V*`, most `id_*`) was excluded for the same reason
things were included: whether it plausibly supports graph traversal or
investigation evidence, not whether TigerGraph could technically hold it.
