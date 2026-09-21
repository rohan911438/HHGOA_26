"""Build a small, relationship-preserving development subset.

DEVELOPMENT FALLBACK TOOLING - targets the Kaggle IEEE-CIS column layout.
NOT official HHGoa material. See docs/development-fallback-analysis.md
and app/dataset/subset.py for the sampling rationale and the exact
columns this assumes (taken from public Kaggle docs, unverified against
real files until scripts/inspect_dataset.py has run against them).

Requires train_transaction.csv (and, if present, train_identity.csv)
under the configured TRANSACTIONS_PATH. Writes transactions_dev.csv
(+ identity_dev.csv) and subset_manifest.json under data/dev/.

Usage:
    python scripts/create_dev_subset.py
    python scripts/create_dev_subset.py --max-rows 5000 --seed 42
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BACKEND_ROOT, get_settings  # noqa: E402
from app.dataset.subset import build_subset, write_subset  # noqa: E402


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Build a development subset (Kaggle fallback)")
    parser.add_argument("--transactions", default=None, help="path to train_transaction.csv")
    parser.add_argument("--identity", default=None, help="path to train_identity.csv")
    parser.add_argument("--out", default="data/dev", help="output directory")
    parser.add_argument("--seed-size", type=int, default=2000, help="rows in the stratified seed sample")
    parser.add_argument("--fraud-fraction", type=float, default=0.5, help="fraction of the seed that is fraud")
    parser.add_argument("--hops", type=int, default=1, help="linkage-expansion hops from the seed")
    parser.add_argument("--max-rows", type=int, default=5000, help="hard cap on the final subset size")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed - deterministic given the same inputs")
    args = parser.parse_args()

    txn_dir = Path(args.transactions) if args.transactions else settings.resolve(settings.transactions_path)
    txn_path = txn_dir if txn_dir.suffix == ".csv" else txn_dir / "train_transaction.csv"

    if not txn_path.exists():
        print(f"ERROR: transaction file not found: {txn_path}", file=sys.stderr)
        print(
            "Run scripts/fetch_dataset.py first, or pass --transactions explicitly.",
            file=sys.stderr,
        )
        return 2

    if args.identity:
        identity_path = Path(args.identity)
    else:
        identity_path = txn_path.parent / "train_identity.csv"
    if not identity_path.exists():
        identity_path = None

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = (BACKEND_ROOT / out_dir).resolve()

    print(f"Indexing {txn_path} ...")
    keep_ids, manifest = build_subset(
        txn_path,
        identity_path,
        seed_size=args.seed_size,
        fraud_fraction=args.fraud_fraction,
        hops=args.hops,
        max_rows=args.max_rows,
        seed=args.seed,
    )
    print(
        f"  source rows: {manifest.source_row_count:,}  "
        f"seed: {manifest.seed_row_count:,}  "
        f"expanded: +{manifest.expanded_row_count:,}  "
        f"final: {manifest.final_row_count:,}"
    )
    for note in manifest.notes:
        print(f"  note: {note}")

    print(f"Writing subset to {out_dir} ...")
    counts = write_subset(txn_path, identity_path, out_dir, keep_ids)
    for name, count in counts.items():
        print(f"  {name}: {count:,} rows written")

    manifest_path = out_dir / "subset_manifest.json"
    manifest_path.write_text(json.dumps(manifest.as_dict(), indent=2), encoding="utf-8")
    print(f"  manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
