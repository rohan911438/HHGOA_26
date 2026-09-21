"""Load a subset into TigerGraph.

Defaults to data/dev/ (the development subset). Never loads the full
590K-row dataset unless --full is passed explicitly - the phase plan is
dev-subset-first, full load only after the dev graph is validated.

Usage:
    python scripts/load_data.py                  # data/dev/
    python scripts/load_data.py --dry-run          # plan only, no network
    python scripts/load_data.py --full              # the whole dataset (large!)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BACKEND_ROOT, get_settings  # noqa: E402
from app.logging import configure_logging  # noqa: E402
from app.tigergraph.client import get_client  # noqa: E402
from app.tigergraph.loader import build_plan, load_plan  # noqa: E402


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Load transaction data into TigerGraph")
    parser.add_argument("--dir", default="data/dev", help="directory with transactions_dev.csv etc.")
    parser.add_argument(
        "--full", action="store_true", help="load the full dataset instead of the dev subset"
    )
    parser.add_argument("--dry-run", action="store_true", help="build the plan, do not upsert")
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()

    configure_logging(level="INFO", json_output=False)

    if args.full:
        txn_path = settings.resolve(settings.dataset_root) / "train_transaction.csv"
        identity_path = settings.resolve(settings.dataset_root) / "train_identity.csv"
        print("WARNING: --full loads the entire dataset (~590K transactions). This may take a while.\n")
    else:
        d = Path(args.dir)
        if not d.is_absolute():
            d = (BACKEND_ROOT / d).resolve()
        txn_path = d / "transactions_dev.csv"
        identity_path = d / "identity_dev.csv"

    if not txn_path.exists():
        print(f"ERROR: {txn_path} not found.", file=sys.stderr)
        if not args.full:
            print("Run scripts/create_dev_subset.py first.", file=sys.stderr)
        return 2
    if not identity_path.exists():
        identity_path = None

    print(f"Building load plan from {txn_path.name}"
          f"{' + ' + identity_path.name if identity_path else ' (no identity file)'} ...")
    started = time.perf_counter()
    plan = build_plan(txn_path, identity_path)
    elapsed = time.perf_counter() - started
    print(f"  built in {elapsed:.1f}s")
    for name, count in sorted(plan.counts().items()):
        print(f"  {name:<24} {count:>8,}")

    if args.dry_run:
        print("\n--dry-run: nothing was sent to TigerGraph.")
        return 0

    client = get_client(settings)
    print(f"\nUpserting to TigerGraph (graph '{settings.tg_graphname}', batch size {args.batch_size}) ...")
    started = time.perf_counter()
    report = load_plan(client, plan, batch_size=args.batch_size)
    elapsed = time.perf_counter() - started

    print(f"\nDone in {elapsed:.1f}s\n")
    print("Vertices:")
    for vtype, r in sorted(report["vertices"].items()):
        print(f"  {vtype:<16} attempted={r['attempted']:>7,}  accepted={r['accepted']:>7,}")
    print("Edges:")
    for etype, r in sorted(report["edges"].items()):
        print(f"  {etype:<16} attempted={r['attempted']:>7,}  accepted={r['accepted']:>7,}")

    if report["errors"]:
        print(f"\n{len(report['errors'])} error(s):", file=sys.stderr)
        for err in report["errors"][:20]:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(
        "\nNote: vertex/edge counts can lag briefly right after a bulk load "
        "(observed live: several seconds of eventual consistency on this "
        "TigerGraph deployment before COUNT queries reflect everything "
        "upserted). If scripts/validate_graph.py's counts look low "
        "immediately after this, re-run it rather than assume data was lost."
    )
    print("\nNext: python scripts/validate_graph.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
