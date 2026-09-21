"""Environment healthcheck.

Verifies, in order:
  1. configuration is present (no secrets printed)
  2. TigerGraph connection works
  3. the graph schema can be retrieved
  4. vertex / edge counts can be retrieved
  5. LLM configuration is present

Usage:
    python scripts/healthcheck.py
    python scripts/healthcheck.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.logging import configure_logging  # noqa: E402
from app.tigergraph import health as tg_health  # noqa: E402
from app.tigergraph.client import get_client  # noqa: E402

SYMBOL = {
    "ok": "[ OK ]",
    "empty": "[EMPTY]",
    "unavailable": "[FAIL]",
    "not_configured": "[SKIP]",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="HHGoa fraud agent healthcheck")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    configure_logging(level="WARNING", json_output=False)
    settings = get_settings()
    client = get_client(settings)

    report = tg_health.full_report(client)

    llm = {
        "name": "llm",
        "status": "ok" if settings.llm_configured else "not_configured",
        "detail": settings.openai_model or "OPENAI_API_KEY / OPENAI_MODEL not set",
        "data": {},
    }
    report["probes"].append(llm)
    report["config"] = settings.redacted()

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0 if report["healthy"] else 1

    print()
    print("=" * 62)
    print(f"  {settings.app_name} v{settings.app_version}  [{settings.app_env}]")
    print("=" * 62)
    print(f"  host        : {settings.tg_host or '(unset)'}")
    print(f"  graph       : {settings.tg_graphname or '(unset)'}")
    print(f"  auth method : {settings.tg_auth_method}")
    print(f"  tgCloud     : {settings.tg_tgcloud}")
    print("-" * 62)

    for probe in report["probes"]:
        mark = SYMBOL.get(probe["status"], "[ ?? ]")
        print(f"  {mark}  {probe['name']:<14} {probe['detail']}")
        data = probe.get("data") or {}
        if probe["name"] == "schema" and data.get("vertex_types"):
            print(f"           vertices: {', '.join(map(str, data['vertex_types']))}")
            print(f"           edges   : {', '.join(map(str, data['edge_types']))}")
        if probe["name"] == "counts" and data.get("vertices"):
            for vtype, count in sorted(data["vertices"].items()):
                print(f"           {vtype:<24} {count:>10,}")

    print("-" * 62)
    print(f"  overall: {'HEALTHY' if report['healthy'] else 'NOT HEALTHY'}")
    print("=" * 62)
    print()

    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
