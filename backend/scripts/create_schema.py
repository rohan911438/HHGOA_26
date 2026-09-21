"""Deploy the HHGOA_FRAUD schema to TigerGraph.

Reproducible: the schema is defined once in app/tigergraph/schema.py and
this script only executes it. Every statement is printed before it runs.
Idempotent-ish: a type or graph that already exists produces a GSQL error
for that one statement, which is reported and does not stop the remaining
statements - re-running after a partial failure is safe.

Refuses to run if TG_READ_ONLY=true. Never touches an existing graph with
a different name; only ever creates GRAPH_NAME.

Usage:
    python scripts/create_schema.py            # deploy
    python scripts/create_schema.py --dry-run   # print statements only
    python scripts/create_schema.py --drop-first  # DESTRUCTIVE, asks first
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.logging import configure_logging  # noqa: E402
from app.tigergraph import schema as schema_def  # noqa: E402
from app.tigergraph.client import TigerGraphError, get_client  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy the HHGOA_FRAUD GSQL schema")
    parser.add_argument("--dry-run", action="store_true", help="print statements, run nothing")
    parser.add_argument(
        "--drop-first",
        action="store_true",
        help="DESTRUCTIVE: drop the existing graph and its types before recreating",
    )
    parser.add_argument("--graph", default=schema_def.GRAPH_NAME, help="graph name to create")
    args = parser.parse_args()

    configure_logging(level="INFO", json_output=False)
    settings = get_settings()

    if settings.tg_read_only and not args.dry_run:
        print("ERROR: TG_READ_ONLY=true - refusing to run schema-creation GSQL.", file=sys.stderr)
        return 2

    statements = schema_def.all_statements(args.graph)

    if args.dry_run:
        print(f"-- {len(statements)} statement(s) for graph '{args.graph}' (dry run, nothing executed)\n")
        for name, stmt in statements:
            print(f"-- {name}")
            print(stmt)
            print()
        return 0

    client = get_client(settings)

    if args.drop_first:
        print(f"About to DROP graph '{args.graph}' and its vertex/edge types.")
        confirm = input("Type the graph name to confirm: ")
        if confirm.strip() != args.graph:
            print("Confirmation did not match - aborted.", file=sys.stderr)
            return 2
        try:
            client.gsql(f"DROP GRAPH {args.graph}", allow_write=True)
            print(f"  dropped graph {args.graph}")
        except TigerGraphError as exc:
            print(f"  (drop graph: {exc})")
        for name in reversed(schema_def.EDGE_NAMES):
            try:
                client.gsql(f"DROP EDGE {name}", allow_write=True)
                print(f"  dropped edge {name}")
            except TigerGraphError as exc:
                print(f"  (drop edge {name}: {exc})")
        for name in reversed(schema_def.VERTEX_NAMES):
            try:
                client.gsql(f"DROP VERTEX {name}", allow_write=True)
                print(f"  dropped vertex {name}")
            except TigerGraphError as exc:
                print(f"  (drop vertex {name}: {exc})")
        print()

    print(f"Deploying schema for graph '{args.graph}' ({len(statements)} statements)\n")
    failures: list[str] = []
    for name, stmt in statements:
        print(f"  {name} ...", end=" ", flush=True)
        try:
            result = client.gsql(stmt, allow_write=True)
            lowered = result.lower()
            if "error" in lowered or "fail" in lowered:
                print("FAILED")
                print(f"    {result.strip()[:300]}")
                failures.append(name)
            else:
                print("ok")
        except TigerGraphError as exc:
            print("FAILED")
            print(f"    {exc}")
            failures.append(name)

    print()
    if failures:
        # Not necessarily fatal: on a re-run, a type that already exists
        # from a previous partial run reports as a "failure" here but is
        # not a real problem - the live-schema verification below is the
        # actual source of truth, not this per-statement text.
        print(f"{len(failures)} statement(s) reported an error: {', '.join(failures)}")
        print("(may be harmless re-run noise, e.g. 'already exists' - verifying against reality below)\n")

    # GSQL can return a success-shaped response for a statement that did
    # not actually register anything (observed live: CREATE VERTEX using
    # a reserved word returned no error text but created nothing usable).
    # Trust the schema catalog, not the free-text response.
    print("Verifying against the live schema catalog (not trusting GSQL's response text) ...")
    try:
        actual = client.get_schema()
        actual_vertices = {v.get("Name") for v in actual.get("VertexTypes", [])}
        actual_edges = {e.get("Name") for e in actual.get("EdgeTypes", [])}
    except TigerGraphError as exc:
        print(f"ERROR: could not read back the deployed schema to verify it: {exc}", file=sys.stderr)
        return 1

    missing_vertices = set(schema_def.VERTEX_NAMES) - actual_vertices
    missing_edges = set(schema_def.EDGE_NAMES) - actual_edges
    if missing_vertices or missing_edges:
        print("ERROR: schema catalog does not match what was requested:", file=sys.stderr)
        if missing_vertices:
            print(f"  missing vertex types: {', '.join(sorted(missing_vertices))}", file=sys.stderr)
        if missing_edges:
            print(f"  missing edge types: {', '.join(sorted(missing_edges))}", file=sys.stderr)
        print(
            "A statement likely reported success without actually registering - "
            "do not trust exit code 0 from a run that skipped this check.",
            file=sys.stderr,
        )
        return 1

    print(f"Verified: all {len(schema_def.VERTEX_NAMES)} vertex types and "
          f"{len(schema_def.EDGE_NAMES)} edge types are present in the live schema catalog.")
    print(f"Schema deployed: graph '{args.graph}'.")
    print("Next: python scripts/test_tigergraph.py   (should now show Graph accessible)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
