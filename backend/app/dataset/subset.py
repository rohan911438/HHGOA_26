"""Relationship-preserving development subset sampling.

DEVELOPMENT FALLBACK TOOLING - targets the Kaggle IEEE-CIS Fraud Detection
column layout (train_transaction.csv + train_identity.csv), used only when
the official HHGOA_IEEE dataset is unavailable. This is NOT official HHGoa
material. See docs/development-fallback-analysis.md.

The column names referenced here (TransactionID, isFraud, card1-6, addr1/2,
P_emaildomain, DeviceInfo, ...) come from Kaggle's public competition
documentation (https://www.kaggle.com/c/ieee-fraud-detection/data), not
from inspecting real files in this environment - the files are not present
yet. This module is UNTESTED against the real Kaggle download; it is
exercised in tests/unit/test_dataset_subset.py against a small synthetic
fixture built to the same column layout, which proves the *sampling
mechanism* is correct, not that the real files match these assumptions.
Re-run scripts/inspect_dataset.py against the actual downloaded CSVs the
moment they exist, and treat any mismatch there as authoritative over the
assumptions here.

Why not plain random-row sampling
----------------------------------
A fraud investigation graph is only interesting because transactions share
structure: the same card, the same billing address, the same email domain,
the same device. IID random sampling of rows would sever almost all of
that structure - a shared-device ring of 6 transactions would very likely
end up with 0 or 1 of its members in a naive random sample. Instead:

  1. Draw a deterministic seed sample, stratified by `isFraud` (fraud rows
     over-represented relative to their true prevalence, since fraud is
     what we most need connected examples of).
  2. Expand outward: pull in every transaction that shares a linking key
     with something already selected (one hop at a time), up to a
     configurable number of hops.
  3. Cap the total size; record exactly how many rows came from seeding
     vs. expansion, and at which hop, so the sample is reproducible and
     its provenance is auditable.

Linking keys used (all present in the public IEEE-CIS column list):

  * card fingerprint  -> (card1, card2, card3, card5)
  * address            -> (addr1, addr2)
  * purchaser email    -> P_emaildomain
  * device              -> DeviceInfo (from the identity table, joined on
                            TransactionID)

`card1`-`card6`, `addr1`/`addr2`, `C1`-`C14`, `D1`-`D15`, `V1`-`V339` and
`id_01`-`id_38` are Vesta/Kaggle-anonymized or engineered features whose
exact real-world meaning is not publicly documented. They are treated here
purely as opaque identifiers/values, never assigned an invented meaning.
"""

from __future__ import annotations

import csv
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# Columns this module reads from train_transaction.csv to build linking
# indexes. Only these (plus TransactionID/isFraud/TransactionDT) are held
# in memory across the whole file; the remaining ~380 columns are read
# only during the streaming write-out pass, one row at a time.
LINK_COLUMNS_TXN = ("card1", "card2", "card3", "card5", "addr1", "addr2", "P_emaildomain")
LINK_COLUMNS_IDENTITY = ("DeviceInfo",)

ID_COLUMN = "TransactionID"
LABEL_COLUMN = "isFraud"


@dataclass
class RowIndex:
    """Lightweight per-transaction record used for sampling decisions.

    Deliberately does not hold the full ~394-column row - that is only
    read again during the streaming write pass, so peak memory is a small
    multiple of (row count x len(LINK_COLUMNS_TXN)), not the full file.
    """

    transaction_id: str
    is_fraud: int | None
    link_keys: dict[str, str]


@dataclass
class SubsetManifest:
    source_transaction_path: str
    source_identity_path: str | None
    source_row_count: int
    seed_row_count: int
    expanded_row_count: int
    final_row_count: int
    max_rows: int
    hops: int
    seed_value: int
    fraud_fraction_in_seed: float
    link_columns: list[str]
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "source_transaction_path": self.source_transaction_path,
            "source_identity_path": self.source_identity_path,
            "source_row_count": self.source_row_count,
            "seed_row_count": self.seed_row_count,
            "expanded_row_count": self.expanded_row_count,
            "final_row_count": self.final_row_count,
            "max_rows": self.max_rows,
            "hops": self.hops,
            "seed_value": self.seed_value,
            "fraud_fraction_in_seed": self.fraud_fraction_in_seed,
            "link_columns": self.link_columns,
            "notes": self.notes,
            "label": "DEVELOPMENT_IEEE_CIS_DATA - NOT OFFICIAL HHGOA BENCHMARK",
        }


def _key(prefix: str, *parts: str) -> str | None:
    """Build a linking key from column values; None if any part is blank."""
    if any(p is None or p.strip() == "" for p in parts):
        return None
    return prefix + ":" + "|".join(p.strip() for p in parts)


def _row_link_keys(row: dict[str, str]) -> dict[str, str]:
    keys: dict[str, str] = {}
    card_key = _key("card", row.get("card1", ""), row.get("card2", ""), row.get("card3", ""), row.get("card5", ""))
    if card_key:
        keys["card"] = card_key
    addr_key = _key("addr", row.get("addr1", ""), row.get("addr2", ""))
    if addr_key:
        keys["addr"] = addr_key
    email_key = _key("email", row.get("P_emaildomain", ""))
    if email_key:
        keys["email"] = email_key
    return keys


def index_transactions(path: Path) -> list[RowIndex]:
    """First pass: build a lightweight index of every transaction row."""
    indexes: list[RowIndex] = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        if ID_COLUMN not in (reader.fieldnames or []):
            raise ValueError(f"{path}: missing required column '{ID_COLUMN}'")
        for row in reader:
            tid = row.get(ID_COLUMN, "").strip()
            if not tid:
                continue
            raw_label = row.get(LABEL_COLUMN, "")
            label = int(raw_label) if raw_label.strip() in ("0", "1") else None
            indexes.append(RowIndex(transaction_id=tid, is_fraud=label, link_keys=_row_link_keys(row)))
    return indexes


def index_device_keys(path: Path | None) -> dict[str, str]:
    """Map TransactionID -> device linking key from train_identity.csv."""
    device_by_txn: dict[str, str] = {}
    if path is None or not path.exists():
        return device_by_txn
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        if ID_COLUMN not in (reader.fieldnames or []):
            return device_by_txn
        for row in reader:
            tid = row.get(ID_COLUMN, "").strip()
            device = row.get("DeviceInfo", "").strip()
            if tid and device:
                device_by_txn[tid] = "device:" + device
    return device_by_txn


def attach_device_keys(indexes: list[RowIndex], device_by_txn: dict[str, str]) -> None:
    for idx in indexes:
        device_key = device_by_txn.get(idx.transaction_id)
        if device_key:
            idx.link_keys["device"] = device_key


def build_key_groups(indexes: list[RowIndex]) -> dict[str, set[str]]:
    """linking key -> set of transaction IDs sharing it (size > 1 only)."""
    groups: dict[str, set[str]] = {}
    for idx in indexes:
        for key in idx.link_keys.values():
            groups.setdefault(key, set()).add(idx.transaction_id)
    return {k: v for k, v in groups.items() if len(v) > 1}


def stratified_seed(
    indexes: list[RowIndex],
    *,
    target_size: int,
    fraud_fraction: float,
    seed: int,
) -> set[str]:
    """Deterministic stratified sample, over-representing fraud rows."""
    rng = random.Random(seed)
    fraud_ids = sorted(idx.transaction_id for idx in indexes if idx.is_fraud == 1)
    clean_ids = sorted(idx.transaction_id for idx in indexes if idx.is_fraud == 0)

    n_fraud_wanted = min(len(fraud_ids), max(1, round(target_size * fraud_fraction)))
    n_clean_wanted = min(len(clean_ids), max(0, target_size - n_fraud_wanted))

    selected = set(rng.sample(fraud_ids, n_fraud_wanted)) if fraud_ids else set()
    selected |= set(rng.sample(clean_ids, n_clean_wanted)) if clean_ids else set()
    return selected


def expand_by_linkage(
    seed_ids: set[str],
    indexes: list[RowIndex],
    key_groups: dict[str, set[str]],
    *,
    hops: int,
    max_rows: int,
) -> set[str]:
    """BFS outward from the seed along shared linking keys."""
    by_id = {idx.transaction_id: idx for idx in indexes}
    selected = set(seed_ids)
    frontier = set(seed_ids)

    for _ in range(max(0, hops)):
        if len(selected) >= max_rows or not frontier:
            break
        next_frontier: set[str] = set()
        for tid in sorted(frontier):
            idx = by_id.get(tid)
            if idx is None:
                continue
            for key in idx.link_keys.values():
                for neighbour in key_groups.get(key, ()):
                    if neighbour not in selected:
                        next_frontier.add(neighbour)
        if len(selected) + len(next_frontier) > max_rows:
            room = max_rows - len(selected)
            next_frontier = set(sorted(next_frontier)[:room])
        selected |= next_frontier
        frontier = next_frontier

    return selected


def build_subset(
    transaction_path: Path,
    identity_path: Path | None,
    *,
    seed_size: int = 2000,
    fraud_fraction: float = 0.5,
    hops: int = 1,
    max_rows: int = 5000,
    seed: int = 42,
) -> tuple[set[str], SubsetManifest]:
    """Compute the selected TransactionID set and its provenance manifest.

    Does not write any files - see write_subset() for that. Kept separate
    so the selection logic can be unit tested against small in-memory /
    temp-file fixtures without needing real Kaggle data.
    """
    indexes = index_transactions(transaction_path)
    device_by_txn = index_device_keys(identity_path)
    attach_device_keys(indexes, device_by_txn)

    key_groups = build_key_groups(indexes)
    seed_ids = stratified_seed(indexes, target_size=seed_size, fraud_fraction=fraud_fraction, seed=seed)
    final_ids = expand_by_linkage(seed_ids, indexes, key_groups, hops=hops, max_rows=max_rows)

    notes = []
    if len(final_ids) < max_rows and len(seed_ids) == len(final_ids):
        notes.append("no linkage expansion occurred - seed rows had no shared linking keys")

    manifest = SubsetManifest(
        source_transaction_path=str(transaction_path),
        source_identity_path=str(identity_path) if identity_path else None,
        source_row_count=len(indexes),
        seed_row_count=len(seed_ids),
        expanded_row_count=len(final_ids) - len(seed_ids),
        final_row_count=len(final_ids),
        max_rows=max_rows,
        hops=hops,
        seed_value=seed,
        fraud_fraction_in_seed=fraud_fraction,
        link_columns=list(LINK_COLUMNS_TXN) + list(LINK_COLUMNS_IDENTITY),
        notes=notes,
    )
    return final_ids, manifest


def _stream_filtered(src: Path, dst: Path, keep_ids: set[str]) -> int:
    """Second pass: copy only selected rows, with every original column."""
    written = 0
    with src.open("r", encoding="utf-8", errors="replace", newline="") as fin, dst.open(
        "w", encoding="utf-8", newline=""
    ) as fout:
        reader = csv.DictReader(fin)
        writer = csv.DictWriter(fout, fieldnames=reader.fieldnames or [])
        writer.writeheader()
        for row in reader:
            if row.get(ID_COLUMN, "").strip() in keep_ids:
                writer.writerow(row)
                written += 1
    return written


def write_subset(
    transaction_path: Path,
    identity_path: Path | None,
    out_dir: Path,
    keep_ids: set[str],
) -> dict[str, int]:
    """Write the filtered transaction (and identity, if present) CSVs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    counts["transactions"] = _stream_filtered(
        transaction_path, out_dir / "transactions_dev.csv", keep_ids
    )
    if identity_path and identity_path.exists():
        counts["identity"] = _stream_filtered(identity_path, out_dir / "identity_dev.csv", keep_ids)
    return counts
