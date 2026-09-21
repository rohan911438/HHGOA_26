# Development Fallback Analysis — IEEE-CIS (Kaggle)

**PROJECT-AUTHORED. NOT OFFICIAL HHGOA MATERIAL.**

This document exists only because the official `HHGOA_IEEE` dataset was not
found in this environment (see `docs/phase-1-report.md` §1 for the full
search record). It describes what can legitimately be derived from the
public Kaggle **IEEE-CIS Fraud Detection** competition, for use as
development-only infrastructure while the real dataset is located.

```
DEVELOPMENT_IEEE_CIS_DATA — NOT THE OFFICIAL HHGOA BENCHMARK
```

Nothing in this file, or in the code it describes
(`app/dataset/subset.py`, `scripts/create_dev_subset.py`), should be read
as a claim about HHGoa's actual fraud policy, typologies, case files, or
20 benchmark cases. None of those exist in this source. Per the explicit
instruction not to fabricate them, this project does **not** contain
`docs/fraud-typologies.md`, `docs/policy-index.md`, or `docs/benchmark.md`
while only the Kaggle fallback is available.

**Status: the dataset has been downloaded and fully inspected.** Real
files, acquired 2026-09-21 via `scripts/fetch_dataset.py` against the
Kaggle API, full-file scan via `scripts/inspect_dataset.py` — see
`docs/dataset-analysis.md` for the authoritative, tested profile (row
counts, null rates, fraud rate, column types, all measured, not assumed).
This document's original purpose — column layout derived from Kaggle's
public docs before real files existed — is now superseded by that report
wherever the two differ; §2 below is left as written for its rationale
(*why* each column is treated as a linking key or opaque feature), but any
number in §2 should be read as approximate and `docs/dataset-analysis.md`
trusted for exact figures.

---

## 1. What the source actually is

Kaggle competition "IEEE-CIS Fraud Detection" (2019), donated by Vesta
Corporation. Two labelled files matter for graph construction:

- `train_transaction.csv` — ~590,540 rows, one per transaction, publicly
  documented as having a `TransactionID` primary key and an `isFraud`
  target label (0/1). Row count and label presence are the two facts this
  document treats as reliable without having downloaded the file, since
  they are stated on the competition's own data page, not inferred.
- `train_identity.csv` — a much smaller file (roughly a quarter of
  transaction rows have a matching identity row), joined to transactions
  via `TransactionID`.

Everything else below — exact column semantics, null rates, distributions
— is **AMBIGUOUS — REQUIRES REVIEW** until the real files are inspected.

## 2. Publicly documented column layout (unverified locally)

| Column(s) | Documented meaning | Treated here as |
| --- | --- | --- |
| `TransactionID` | unique transaction key | candidate vertex primary ID |
| `isFraud` | 0/1 fraud label | training-only signal, not a graph attribute an investigation agent would have at inference time for a *new* transaction |
| `TransactionDT` | seconds offset from an undisclosed reference timestamp | ordering signal only — **not** a real calendar date |
| `TransactionAmt` | transaction amount | transaction attribute |
| `ProductCD` | product code (`W`, `H`, `C`, `S`, `R`) | transaction attribute |
| `card1`–`card6` | card-identifying features; `card4`/`card6` are categorical (network/debit-credit), the rest numeric-coded | `card1`,`card2`,`card3`,`card5` used here as a composite "card fingerprint" linking key — **not** a real card number, which Vesta never discloses |
| `addr1`, `addr2` | numeric-coded address fields | linking key |
| `dist1`, `dist2` | distance features | transaction attribute |
| `P_emaildomain`, `R_emaildomain` | purchaser / recipient email domain | linking key (`P_emaildomain` only, here) |
| `C1`–`C14` | "counting" features | **opaque** — Vesta does not disclose what is counted |
| `D1`–`D15` | timedelta features | **opaque** — disclosed as time-deltas, not what event they're between |
| `M1`–`M9` | match flags (T/F) | **opaque** — disclosed as "matches", not what is compared |
| `V1`–`V339` | Vesta-engineered features | **opaque** — no public semantic mapping exists |
| `id_01`–`id_38` (identity file) | identity/behavioural signals | **opaque**, mix of numeric and categorical |
| `DeviceType`, `DeviceInfo` (identity file) | device class / description string | linking key (`DeviceInfo`) |

**The `C*`, `D*`, `M*`, `V*`, and `id_*` columns are not assigned any
invented meaning anywhere in this codebase.** They are carried through
as opaque attributes if loaded at all; no code treats them as graph
structure.

## 3. What this justifies building now

Only the mechanism, not a schema commitment:

- `app/dataset/subset.py` — relationship-preserving sampling keyed on the
  four linking columns above (card fingerprint, address, purchaser email,
  device). Verified with 10 unit tests against a **synthetic** fixture
  built to this column layout (`tests/unit/test_dataset_subset.py`) —
  those tests prove the sampling *mechanism* (determinism, linkage
  expansion, size capping) is correct; they prove nothing about whether
  real Kaggle rows actually cluster this way at scale.
- `scripts/create_dev_subset.py` — thin CLI over the above. Run and
  confirmed to fail cleanly (exit 2, clear message) when the source CSVs
  aren't present — verified, since that's the actual current state.

## 4. What this now justifies (dataset inspected as of 2026-09-21)

- `docs/tigergraph-schema.md` — the real null-rate and cardinality numbers
  needed to make schema decisions now exist in `docs/dataset-analysis.md`.
  Not yet written; next step.
- Running `scripts/create_dev_subset.py` for real against the downloaded
  files, not just the synthetic-fixture unit tests.

## 4b. Still not justified

- Any fraud typology, policy threshold, or benchmark case — this source
  contains none of those, regardless of how thoroughly it's inspected.
  See the repeated prohibition in `docs/phase-1-report.md`. Any typology
  built later from this data's graph structure must be empirically
  derived and marked `PROJECT-AUTHORED`, never presented as official.

## 5. Re-verification checklist once files exist

```bash
python scripts/fetch_dataset.py            # downloads into data/hhgoa/
python scripts/inspect_dataset.py          # docs/dataset-analysis.md
python scripts/create_dev_subset.py        # data/dev/ + manifest
```

Any place this document's assumed column layout turns out to be wrong,
`docs/dataset-analysis.md` (generated from the real files) wins.
