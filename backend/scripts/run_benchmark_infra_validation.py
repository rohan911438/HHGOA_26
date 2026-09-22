"""Phase 2J.5 - runs infrastructure validation for real and writes the
machine-readable output.

This is NOT the official HHGoa 20-case benchmark - the official dataset
was not found in this repository/environment (see
app/benchmark/discovery.py, docs/phase-2-benchmark-report.md). It runs
`app.benchmark.run_infrastructure_validation` against real TigerGraph
data (the IEEE-CIS development-fallback subset already loaded by earlier
phases) with a deterministic FakeLLMClient, and writes:

    benchmark/results/results.json   - every BenchmarkResult, full detail
    benchmark/results/summary.json   - counts only, diagnostic metrics
    benchmark/results/per_case/<txn_id>.json

Every record in these files carries "is_official_benchmark_case": false.
No score is computed - there is no official expected answer to score
against. Safe to run repeatedly; never writes secrets.

Usage:
    python scripts/run_benchmark_infra_validation.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError, OSError):
    pass

from app.benchmark import discover_official_benchmark, run_infrastructure_validation
from app.case import CaseTrigger, InMemoryCaseStore
from app.config import get_settings
from app.tigergraph.client import get_client
from app.tigergraph.diagnostics import all_passed, run_checks

OUTPUT_ROOT = Path(__file__).resolve().parent.parent / "benchmark" / "results"

DEV_FALLBACK_TXN_IDS = ["2987937", "2987000", "2987001", "2987002", "2987003"]


def main() -> int:
    settings = get_settings()

    discovery = discover_official_benchmark(settings)
    print("Official dataset available:", discovery.official_dataset_available)
    for note in discovery.notes:
        print(" -", note)

    if not settings.tg_configured:
        print("TigerGraph not configured - cannot run infrastructure validation.")
        return 2

    client = get_client(settings)
    checks = run_checks(settings, client)
    if not all_passed(checks):
        detail = next((c.detail for c in checks if c.ok is False), "unknown failure")
        print("TigerGraph not currently reachable/healthy:", detail)
        return 2

    store = InMemoryCaseStore()
    results = run_infrastructure_validation(
        client, store, DEV_FALLBACK_TXN_IDS, trigger=CaseTrigger.FRAUD_SIGNAL
    )

    per_case_dir = OUTPUT_ROOT / "per_case"
    per_case_dir.mkdir(parents=True, exist_ok=True)

    results_payload = [json.loads(r.model_dump_json()) for r in results]
    (OUTPUT_ROOT / "results.json").write_text(
        json.dumps(
            {
                "is_official_benchmark": False,
                "label": "PHASE 2J INFRASTRUCTURE VALIDATION - diagnostic project metric, "
                "NOT an official HHGoa benchmark score.",
                "official_dataset_available": discovery.official_dataset_available,
                "results": results_payload,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    for r, payload in zip(results, results_payload, strict=True):
        (per_case_dir / f"{r.transaction_id}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    completed = [r for r in results if r.error is None]
    failed = [r for r in results if r.error is not None]
    summary = {
        "is_official_benchmark": False,
        "official_dataset_available": discovery.official_dataset_available,
        "cases_executed": len(results),
        "cases_completed": len(completed),
        "cases_failed": len(failed),
        "failed_transaction_ids": [r.transaction_id for r in failed],
        "note": "Diagnostic project metrics only - see docs/phase-2-benchmark-report.md.",
    }
    (OUTPUT_ROOT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
