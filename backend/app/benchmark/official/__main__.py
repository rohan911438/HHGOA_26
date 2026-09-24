"""Official HHGOA_IEEE benchmark CLI.

    python -m app.benchmark.official fetch        # download the package from TigerGraph's Drive folder
    python -m app.benchmark.official validate     # check the package against its README; write manifest
    python -m app.benchmark.official prepare      # build slim load files (+ verify card-id rule)
    python -m app.benchmark.official load-graph   # create HHGOA_IEEE graph + bulk load
    python -m app.benchmark.official run [--case HHG-001]   # investigate, write answers, write to graph
    python -m app.benchmark.official all          # validate -> run all 20 -> conformance -> summary

Never falls back to the IEEE-CIS development data: with no official
package, every command exits with "ERROR: OFFICIAL HHGOA DATASET UNAVAILABLE".
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

from app.benchmark.official.dataset import (
    DEFAULT_ROOT,
    OfficialDatasetUnavailable,
    fetch_official_package,
    load_official_cases,
    validate_official_package,
)
from app.config import BACKEND_ROOT

REPO_ROOT = BACKEND_ROOT.parent
ANSWERS_DIR = REPO_ROOT / "cases"
RESULTS_DIR = BACKEND_ROOT / "benchmark" / "results" / "official"


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, indent=2, default=str, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def cmd_validate(root: Path) -> int:
    rep = validate_official_package(root)
    _dump(RESULTS_DIR / "manifest.json", rep.as_manifest())
    for c in rep.checks:
        print(f"  [{'OK' if c['ok'] else 'FAIL'}] {c['check']} {c['detail']}")
    print(
        f"official={rep.official and rep.ok} cases={len(rep.cases)} -> {RESULTS_DIR / 'manifest.json'}"
    )
    return 0 if rep.ok else 2


def _reference_sets(root: Path) -> dict:
    import pandas as pd

    from app.benchmark.official.graph_load import read_transactions

    tx = read_transactions(root)
    cc = pd.read_csv(root / "closed_cases_history.csv", usecols=["case_id"])
    return {
        "txn_amounts": dict(
            zip(tx["TransactionID"].astype(str), tx["TransactionAmt"].astype(float), strict=True)
        ),
        "card_ids": set(tx["card_id"]),
        "customer_ids": set(tx["customer_id"]),
        # official case ids appear in evidence when earlier agent cases are recalled
        "closed_case_ids": set(cc["case_id"]),
        "official_case_ids": {c.case_id for c in load_official_cases(root)},
        "device_profiles": set(tx["device_profile"]) - {""},
    }


def _write_case_artifacts(run, checks: list[dict]) -> None:
    a, tr, case = run.answer, run.trace, run.case
    d = RESULTS_DIR / case.case_id
    _dump(d / "input.json", {**vars(case), "source": "official case_pack.csv"})
    _dump(
        d / "initial-investigation.json",
        {
            "flagged_transaction": tr["flagged"],
            "card_history_size": tr["card_history_size"],
            "customer_cards": tr["customer_cards"],
            "device_neighbourhood": tr["device_neighbourhood"],
            "signals": tr["signals"],
            "episode_txn_ids": tr["episode_txn_ids"],
            "initial_assessment": tr["initial_assessment"],
            "own_closed_cases": tr["own_closed_cases"],
            "similar_closed_cases_retrieved": tr["similar_retrieved"],
            "agent_case_memory": tr["agent_case_memory"],
        },
    )
    _dump(
        d / "initial-decision.json",
        {
            "fraud_probability_before_evidence": tr["initial_assessment"]["probability"],
            "next_best_actions_initial": a["next_best_actions"]["initial"],
            "additional_evidence_required": bool(a["evidence_requests"]),
            "why": (
                f"Requested: {a['evidence_requests'][0]['type']} (policy R1/§3b - probability "
                f"{tr['initial_assessment']['probability']} not at a §6 stopping point)"
                if a["evidence_requests"]
                else f"Not requested: {a['stop_reason']}"
            ),
        },
    )
    _dump(
        d / "additional-evidence.json",
        {
            "additional_evidence_required": bool(a["evidence_requests"]),
            "evidence_requests": a["evidence_requests"],
            "note": "Replies are simulated per README §5; the assumption is stated.",
        },
    )
    _dump(
        d / "final-investigation.json",
        {
            k: a["case"][k]
            for k in (
                "status",
                "verdict",
                "fraud_probability",
                "pattern",
                "pattern_description",
                "affected_txn_ids",
                "first_suspicious_txn_id",
                "connected_card_ids",
                "connected_device_profiles",
                "exposure_usd",
            )
        },
    )
    _dump(
        d / "final-decision.json",
        {
            "next_best_actions_final": a["next_best_actions"]["final"],
            "what_changed": a["next_best_actions"]["what_changed"],
            "stop_reason": a["stop_reason"],
        },
    )
    _dump(d / "case-record.json", a["case"])
    _dump(d / "graph-verification.json", tr["graph_write"])
    _dump(d / "sar.json", a["sar"] if a["sar"]["file"] else {"sar_required": False, **a["sar"]})
    _dump(
        d / "benchmark-comparison.json",
        {
            "official_answer_key_available": False,
            "note": "The HHGOA_IEEE package ships no answer key (README: 'We score them against an answer key you "
            "don't have'). No accuracy is computed. These are deterministic format/policy conformance checks.",
            "conformance_passed": sum(c["ok"] for c in checks),
            "conformance_total": len(checks),
            "checks": checks,
        },
    )
    _dump(
        d / "trace.json",
        {"steps": tr["steps"], "tool_calls": tr["tool_calls"], "decision": tr["decision"]},
    )


def cmd_run(root: Path, only: list[str] | None, write_graph: bool = True) -> int:
    from app.benchmark.official.agent import OfficialInvestigator
    from app.benchmark.official.graph_load import official_client
    from app.benchmark.official.tools import OfficialGraphTools
    from app.benchmark.official.validate import check_answer

    cases = sorted(
        load_official_cases(root), key=lambda c: c.opened_at
    )  # chronological: memory accrues
    if only:
        cases = [c for c in cases if c.case_id in only]
    refs = _reference_sets(root)
    client = official_client()
    if not only and write_graph:
        # A full run replays the cases in date order; clear the agent's own
        # earlier case vertices so no case can "remember" a later one.
        # Only InvestigationCase (the agent's output) is touched.
        n = client.connection.delVertices("InvestigationCase")
        print(f"  cleared {n} earlier InvestigationCase vertices")
    tools = OfficialGraphTools(client)
    agent = OfficialInvestigator(tools, write_to_graph=write_graph)
    rows = []
    for case in cases:
        try:
            run = agent.run(case)
        except Exception as exc:  # noqa: BLE001 - recorded per case, never hidden
            print(f"  {case.case_id} FAILED {type(exc).__name__}: {exc}")
            rows.append(
                {
                    "case_id": case.case_id,
                    "executed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            _dump(RESULTS_DIR / case.case_id / "error.json", rows[-1])
            continue
        checks = check_answer(run.answer, **refs)
        _dump(ANSWERS_DIR / f"{case.case_id}.json", run.answer)
        _write_case_artifacts(run, checks)
        a = run.answer
        row = {
            "case_id": case.case_id,
            "executed": True,
            "trigger": case.trigger_type,
            "flagged_txn_id": case.flagged_txn_id,
            "card_id": case.card_id,
            "verdict": a["case"]["verdict"],
            "status": a["case"]["status"],
            "pattern": a["case"]["pattern"],
            "fraud_probability_initial": run.trace["initial_assessment"]["probability"],
            "fraud_probability_final": a["case"]["fraud_probability"],
            "initial_actions": "|".join(
                f"{r['action']}:{r['route']}" for r in a["next_best_actions"]["initial"]
            ),
            "final_actions": "|".join(
                f"{r['action']}:{r['route']}" for r in a["next_best_actions"]["final"]
            ),
            "evidence_requested": "|".join(r["type"] for r in a["evidence_requests"]) or "none",
            "exposure_usd": a["case"]["exposure_usd"],
            "affected_txns": len(a["case"]["affected_txn_ids"]),
            "connected_cards": len(a["case"]["connected_card_ids"]),
            "sar_file": a["sar"]["file"],
            "written_to_graph": a["case"]["written_to_graph"],
            "graph_case_id": a["case"]["graph_case_id"],
            "similar_prior_cases": "|".join(a["case"]["similar_prior_cases"]),
            "tool_calls": a["tool_calls"],
            "latency_s": a["latency_s"],
            "conformance": f"{sum(c['ok'] for c in checks)}/{len(checks)}",
            "conformance_failures": "|".join(c["check"] for c in checks if not c["ok"]),
        }
        rows.append(row)
        print(
            f"  {case.case_id} {row['verdict']:<10} {row['pattern']:<28} p {row['fraud_probability_initial']}->"
            f"{row['fraud_probability_final']}  final={row['final_actions']}  graph={row['written_to_graph']}  "
            f"conformance={row['conformance']}"
        )
    if not only:
        _write_summary(rows)
    return 0 if all(r.get("executed") for r in rows) else 1


def _write_summary(rows: list[dict]) -> None:
    ex = [r for r in rows if r.get("executed")]
    n = len(rows)

    def frac(k: int, d: int) -> dict:
        return {
            "numerator": k,
            "denominator": d,
            "percentage": round(100 * k / d, 1) if d else None,
        }

    summary = {
        "dataset": "HHGOA_IEEE",
        "official": True,
        "case_count": n,
        "answer_key_available": False,
        "accuracy_metrics": "NOT AVAILABLE IN OFFICIAL BENCHMARK SCHEMA - the package ships no answer key; "
        "TigerGraph scores submissions privately.",
        "execution": {
            "cases_executed": frac(len(ex), n),
            "written_to_graph_and_read_back": frac(sum(1 for r in ex if r["written_to_graph"]), n),
            "fully_conformant_answers": frac(
                sum(1 for r in ex if not r["conformance_failures"]), n
            ),
            "initial_and_final_nba_recorded": frac(len(ex), n),
        },
        "observed_distribution": {
            "verdict": _count(ex, "verdict"),
            "pattern": _count(ex, "pattern"),
            "status": _count(ex, "status"),
            "trigger": _count(ex, "trigger"),
            "sar_filed": sum(1 for r in ex if r["sar_file"]),
            "evidence_requested": sum(1 for r in ex if r["evidence_requested"] != "none"),
            "recommendation_changed_after_evidence": sum(
                1 for r in ex if r["initial_actions"] != r["final_actions"]
            ),
        },
        "cases": rows,
    }
    _dump(RESULTS_DIR / "summary.json", summary)
    with (RESULTS_DIR / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        keys = list(rows[0].keys()) if ex else ["case_id", "executed", "error"]
        w = csv.DictWriter(
            fh,
            fieldnames=sorted(
                {k for r in rows for k in r},
                key=lambda k: (k not in keys, keys.index(k) if k in keys else 0),
            ),
        )
        w.writeheader()
        w.writerows(rows)
    print(f"summary -> {RESULTS_DIR / 'summary.json'}")


def _count(rows: list[dict], key: str) -> dict:
    out: dict = {}
    for r in rows:
        out[r[key]] = out.get(r[key], 0) + 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.benchmark.official")
    ap.add_argument("command", choices=["fetch", "validate", "prepare", "load-graph", "run", "all"])
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--case", action="append", help="run only this case id (repeatable)")
    ap.add_argument("--no-graph-write", action="store_true")
    args = ap.parse_args(argv)
    logging.disable(logging.INFO)
    root = Path(args.root)
    try:
        if args.command == "fetch":
            print(fetch_official_package(root))
            return 0
        if args.command == "validate":
            return cmd_validate(root)
        if args.command == "prepare":
            from app.benchmark.official.graph_load import prepare

            print(prepare(root))
            return 0
        if args.command == "load-graph":
            from app.benchmark.official.graph_load import deploy_schema, load, official_client
            from app.tigergraph.client import get_client

            print(deploy_schema(get_client()))
            print(load(official_client(), root))
            return 0
        if args.command == "run":
            return cmd_run(root, args.case, not args.no_graph_write)
        if args.command == "all":
            rc = cmd_validate(root)
            if rc:
                return rc
            return cmd_run(root, None, not args.no_graph_write)
    except OfficialDatasetUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
