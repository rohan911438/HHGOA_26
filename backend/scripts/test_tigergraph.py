"""Phase 1 - TigerGraph connection verification.

Runs the specific checklist required before any schema/agent work begins:

    [ ] Host reachable
    [ ] Authentication successful
    [ ] Graph listing (if supported)
    [ ] Graph exists
    [ ] Schema accessible
    [ ] Read-only query succeeds

Every failure is classified (CONFIGURATION / AUTHENTICATION / NETWORK /
ENDPOINT / GRAPH / SDK / SERVER) - see app/tigergraph/diagnostics.py for
the classification logic, which is shared with the future
/health/tigergraph API endpoint.

This script performs no writes, no schema changes, and no CREATE/DROP
statements. It is safe to run against a production database.

Usage:
    python scripts/test_tigergraph.py
    python scripts/test_tigergraph.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles often default to a codepage (cp1252) that cannot encode
# the check/cross marks below. Force UTF-8 on stdout when possible; fall
# back to ASCII-safe symbols rather than crash mid-report.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    _UNICODE_OK = True
except (AttributeError, ValueError, OSError):
    _UNICODE_OK = False

from app.config import get_settings  # noqa: E402
from app.logging import configure_logging  # noqa: E402
from app.tigergraph.diagnostics import CheckResult, all_passed, run_checks  # noqa: E402

if _UNICODE_OK:
    CHECK, CROSS, SKIP = "✓", "✗", "○"
else:
    CHECK, CROSS, SKIP = "[OK]", "[X]", "[-]"


def symbol(r: CheckResult) -> str:
    if r.ok is None:
        return SKIP
    return CHECK if r.ok else CROSS


def line(r: CheckResult) -> str:
    tag = f" [{r.category}]" if r.category else ""
    return f"  {symbol(r)} {r.name}{tag}" + (f" - {r.detail}" if r.detail else "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the TigerGraph Savanna connection")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    configure_logging(level="WARNING", json_output=False)
    settings = get_settings()
    results = run_checks(settings)
    healthy = all_passed(results)

    if args.json:
        print(json.dumps({"healthy": healthy, "checks": [r.as_dict() for r in results]}, indent=2))
        return 0 if healthy else 1

    print()
    print("TigerGraph connection verification")
    print("=" * 62)
    for r in results:
        print(line(r))
    print("=" * 62)
    print(f"Result: {'ALL CHECKS PASSED' if healthy else 'FAILED - see classification above'}")
    print()
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
