"""Phase 1 - dataset inspection.

Profiles whatever is under DATASET_ROOT without assuming any particular
shape. For every file it reports structure; for tabular files it streams
a sample and infers column types, null rates, cardinality and candidate
keys. It never modifies the source dataset.

Output:
    docs/dataset-analysis.md   (human-readable report)
    artifacts/dataset-profile.json  (machine-readable profile)

Usage:
    python scripts/inspect_dataset.py
    python scripts/inspect_dataset.py --root ./data/hhgoa --sample 50000
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BACKEND_ROOT, get_settings  # noqa: E402

TABULAR = {".csv", ".tsv", ".psv"}
JSONISH = {".json", ".jsonl", ".ndjson"}
TEXTISH = {".md", ".txt", ".rst", ".yaml", ".yml"}
BINARY = {".pdf", ".docx", ".xlsx", ".parquet", ".zip", ".gz"}

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d*\.\d+([eE][-+]?\d+)?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?")
_BOOL_VALUES = {"true", "false", "t", "f", "yes", "no", "0", "1"}
_NULLISH = {"", "na", "n/a", "null", "none", "nan", "-", "?"}


def human_bytes(n: int) -> str:
    step = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if step < 1024:
            return f"{step:.1f} {unit}"
        step /= 1024
    return f"{step:.1f} PB"


def sniff_delimiter(path: Path) -> str:
    if path.suffix == ".tsv":
        return "\t"
    if path.suffix == ".psv":
        return "|"
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        head = fh.read(16384)
    try:
        return csv.Sniffer().sniff(head, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def infer_type(values: list[str]) -> str:
    """Infer a column type from non-null sample values."""
    if not values:
        return "unknown"
    kinds: Counter[str] = Counter()
    for v in values:
        s = v.strip()
        if _INT_RE.match(s):
            kinds["int"] += 1
        elif _FLOAT_RE.match(s):
            kinds["float"] += 1
        elif _DATE_RE.match(s):
            kinds["datetime"] += 1
        else:
            kinds["string"] += 1

    lowered = {v.strip().lower() for v in values}
    if lowered and lowered <= _BOOL_VALUES and len(lowered) <= 2:
        return "bool"

    total = sum(kinds.values())
    if kinds["int"] == total:
        return "int"
    if kinds["int"] + kinds["float"] == total:
        return "float"
    if kinds["datetime"] == total:
        return "datetime"
    if kinds["string"] == total:
        return "string"
    return "mixed"


def profile_tabular(path: Path, sample_limit: int) -> dict[str, Any]:
    delim = sniff_delimiter(path)
    columns: list[str] = []
    samples: dict[str, list[str]] = {}
    distinct: dict[str, set[str]] = {}
    nulls: Counter[str] = Counter()
    distinct_capped: set[str] = set()
    rows_scanned = 0
    ragged_rows = 0
    parse_errors: list[str] = []
    DISTINCT_CAP = 50_000

    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh, delimiter=delim)
        try:
            columns = next(reader)
        except StopIteration:
            return {"kind": "tabular", "error": "file is empty", "path": str(path)}

        columns = [c.strip().lstrip("﻿") for c in columns]
        samples = {c: [] for c in columns}
        distinct = {c: set() for c in columns}

        for row in reader:
            rows_scanned += 1
            if len(row) != len(columns):
                ragged_rows += 1
                if len(parse_errors) < 5:
                    parse_errors.append(
                        f"row {rows_scanned}: {len(row)} fields, expected {len(columns)}"
                    )
            for col, val in zip(columns, row):
                if val.strip().lower() in _NULLISH:
                    nulls[col] += 1
                    continue
                if len(samples[col]) < 200:
                    samples[col].append(val)
                if col not in distinct_capped:
                    distinct[col].add(val)
                    if len(distinct[col]) > DISTINCT_CAP:
                        distinct_capped.add(col)
            if rows_scanned >= sample_limit:
                break

    # Approximate total rows when we stopped early.
    truncated = rows_scanned >= sample_limit

    col_profiles = []
    for col in columns:
        n_distinct = len(distinct[col])
        non_null = rows_scanned - nulls[col]
        col_profiles.append(
            {
                "name": col,
                "inferred_type": infer_type(samples[col]),
                "null_count": nulls[col],
                "null_pct": round(100 * nulls[col] / rows_scanned, 2) if rows_scanned else 0.0,
                "distinct": n_distinct,
                "distinct_capped": col in distinct_capped,
                "unique_ratio": round(n_distinct / non_null, 4) if non_null else 0.0,
                "candidate_key": bool(non_null) and n_distinct == non_null and non_null > 1,
                "examples": samples[col][:5],
            }
        )

    return {
        "kind": "tabular",
        "delimiter": delim,
        "columns": len(columns),
        "rows_scanned": rows_scanned,
        "truncated": truncated,
        "ragged_rows": ragged_rows,
        "parse_errors": parse_errors,
        "column_profiles": col_profiles,
    }


def profile_json(path: Path, sample_limit: int) -> dict[str, Any]:
    text_head = path.open("r", encoding="utf-8", errors="replace").read(4_000_000)
    if path.suffix in (".jsonl", ".ndjson"):
        records = []
        for line in text_head.splitlines()[:sample_limit]:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        keys: Counter[str] = Counter()
        for rec in records:
            if isinstance(rec, dict):
                keys.update(rec.keys())
        return {
            "kind": "jsonl",
            "records_scanned": len(records),
            "keys": dict(keys.most_common()),
            "example": records[0] if records else None,
        }

    try:
        data = json.loads(text_head)
    except json.JSONDecodeError as exc:
        return {"kind": "json", "error": f"could not parse: {exc}"}

    if isinstance(data, list):
        keys = Counter()
        for rec in data[:sample_limit]:
            if isinstance(rec, dict):
                keys.update(rec.keys())
        return {
            "kind": "json-array",
            "length": len(data),
            "keys": dict(keys.most_common()),
            "example": data[0] if data else None,
        }
    if isinstance(data, dict):
        return {
            "kind": "json-object",
            "top_level_keys": list(data.keys()),
            "example": {k: data[k] for k in list(data)[:3]},
        }
    return {"kind": "json", "scalar": True}


def profile_text(path: Path) -> dict[str, Any]:
    text = path.open("r", encoding="utf-8", errors="replace").read()
    lines = text.splitlines()
    headings = [ln.strip() for ln in lines if ln.strip().startswith("#")][:40]
    return {
        "kind": "text",
        "lines": len(lines),
        "characters": len(text),
        "headings": headings,
        "head": "\n".join(lines[:40]),
    }


def walk(root: Path, sample_limit: int) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() or path.name.startswith("."):
            continue
        rel = path.relative_to(root)
        size = path.stat().st_size
        entry: dict[str, Any] = {
            "path": str(rel).replace("\\", "/"),
            "size_bytes": size,
            "size_human": human_bytes(size),
            "suffix": path.suffix.lower(),
        }
        try:
            if path.suffix.lower() in TABULAR:
                entry["profile"] = profile_tabular(path, sample_limit)
            elif path.suffix.lower() in JSONISH:
                entry["profile"] = profile_json(path, sample_limit)
            elif path.suffix.lower() in TEXTISH:
                entry["profile"] = profile_text(path)
            elif path.suffix.lower() in BINARY:
                entry["profile"] = {"kind": "binary", "note": "needs a dedicated reader"}
            else:
                entry["profile"] = {"kind": "unknown"}
        except Exception as exc:
            entry["profile"] = {"kind": "error", "error": f"{type(exc).__name__}: {exc}"}
        entries.append(entry)
    return entries


def render_markdown(root: Path, entries: list[dict[str, Any]]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out: list[str] = []
    out.append("# Dataset Analysis\n")
    out.append(f"_Generated by `scripts/inspect_dataset.py` on {now}._\n")
    out.append(f"**Dataset root:** `{root}`\n")
    out.append(
        "This report is generated from the files as they exist on disk. "
        "The source dataset is never modified.\n"
    )

    total = sum(e["size_bytes"] for e in entries)
    out.append("## 1. Inventory\n")
    out.append(f"{len(entries)} files, {human_bytes(total)} total.\n")
    out.append("| File | Size | Kind | Shape |")
    out.append("| --- | --- | --- | --- |")
    for e in entries:
        p = e["profile"]
        kind = p.get("kind", "?")
        if kind == "tabular":
            shape = f"{p.get('columns', '?')} cols x {p.get('rows_scanned', '?'):,} rows scanned"
            if p.get("truncated"):
                shape += " (truncated)"
        elif kind in ("json-array", "jsonl"):
            shape = f"{p.get('length', p.get('records_scanned', '?'))} records"
        elif kind == "json-object":
            shape = f"{len(p.get('top_level_keys', []))} top-level keys"
        elif kind == "text":
            shape = f"{p.get('lines', '?')} lines"
        else:
            shape = ""
        out.append(f"| `{e['path']}` | {e['size_human']} | {kind} | {shape} |")
    out.append("")

    out.append("## 2. Tabular files\n")
    tabular = [e for e in entries if e["profile"].get("kind") == "tabular"]
    if not tabular:
        out.append("_No tabular files found._\n")
    for e in tabular:
        p = e["profile"]
        out.append(f"### `{e['path']}`\n")
        out.append(
            f"- delimiter: `{p['delimiter']}`  \n"
            f"- rows scanned: {p['rows_scanned']:,}"
            f"{' (sample limit reached - file is larger)' if p['truncated'] else ' (entire file)'}  \n"
            f"- ragged rows: {p['ragged_rows']}\n"
        )
        if p["parse_errors"]:
            out.append("**Parse issues:**\n")
            for err in p["parse_errors"]:
                out.append(f"- {err}")
            out.append("")
        out.append("| Column | Type | Null % | Distinct | Unique ratio | Key? | Examples |")
        out.append("| --- | --- | --- | --- | --- | --- | --- |")
        for c in p["column_profiles"]:
            ex = ", ".join(str(x)[:24] for x in c["examples"][:3])
            ex = ex.replace("|", "\\|")
            distinct = f"{c['distinct']:,}{'+' if c['distinct_capped'] else ''}"
            out.append(
                f"| `{c['name']}` | {c['inferred_type']} | {c['null_pct']}% | {distinct} "
                f"| {c['unique_ratio']} | {'yes' if c['candidate_key'] else ''} | {ex} |"
            )
        out.append("")

    out.append("## 3. JSON files\n")
    jsons = [e for e in entries if str(e["profile"].get("kind", "")).startswith("json")]
    if not jsons:
        out.append("_No JSON files found._\n")
    for e in jsons:
        p = e["profile"]
        out.append(f"### `{e['path']}`\n")
        out.append("```json")
        out.append(json.dumps(p, indent=2, default=str)[:3000])
        out.append("```\n")

    out.append("## 4. Documents\n")
    docs = [e for e in entries if e["profile"].get("kind") in ("text", "binary")]
    if not docs:
        out.append("_No documents found._\n")
    for e in docs:
        p = e["profile"]
        out.append(f"### `{e['path']}`\n")
        if p.get("kind") == "binary":
            out.append("_Binary document - requires a dedicated reader._\n")
            continue
        if p.get("headings"):
            out.append("Headings:\n")
            for h in p["headings"]:
                out.append(f"- {h}")
            out.append("")
        out.append("<details><summary>First 40 lines</summary>\n")
        out.append("```")
        out.append(p.get("head", ""))
        out.append("```\n</details>\n")

    out.append("## 5. Data quality observations\n")
    observations: list[str] = []
    for e in tabular:
        p = e["profile"]
        if p["ragged_rows"]:
            observations.append(
                f"`{e['path']}`: {p['ragged_rows']} rows with an unexpected field count."
            )
        for c in p["column_profiles"]:
            if c["null_pct"] > 50:
                observations.append(
                    f"`{e['path']}`.`{c['name']}`: {c['null_pct']}% null - "
                    "may not be usable as a graph attribute."
                )
            if c["inferred_type"] == "mixed":
                observations.append(
                    f"`{e['path']}`.`{c['name']}`: mixed types - needs normalisation on load."
                )
    if observations:
        for o in observations[:60]:
            out.append(f"- {o}")
    else:
        out.append("_No automated observations. Review the tables above manually._")
    out.append("")

    out.append("## 6. Entity / relationship candidates\n")
    out.append(
        "_Columns flagged `Key? = yes` are candidate vertex primary IDs. Columns that "
        "repeat across files are candidate join keys and therefore candidate edges. "
        "Confirm these against the dataset README before designing the schema._\n"
    )
    key_index: dict[str, list[str]] = {}
    for e in tabular:
        for c in e["profile"]["column_profiles"]:
            key_index.setdefault(c["name"], []).append(e["path"])
    shared = {k: v for k, v in key_index.items() if len(v) > 1}
    if shared:
        out.append("| Column | Appears in |")
        out.append("| --- | --- |")
        for col, files in sorted(shared.items()):
            out.append(f"| `{col}` | {', '.join(f'`{f}`' for f in files)} |")
    else:
        out.append("_No columns shared between tabular files._")
    out.append("")

    out.append("## 7. Open questions\n")
    out.append("_To be completed by hand after reading the dataset README._\n")
    return "\n".join(out)


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Inspect the HHGoa dataset")
    parser.add_argument("--root", default=settings.dataset_root, help="dataset root directory")
    parser.add_argument(
        "--sample", type=int, default=200_000, help="max rows to scan per tabular file"
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_absolute():
        root = (BACKEND_ROOT / root).resolve()

    if not root.exists():
        print(f"ERROR: dataset root does not exist: {root}", file=sys.stderr)
        print("Set DATASET_ROOT in .env or pass --root.", file=sys.stderr)
        return 2

    entries = walk(root, args.sample)
    if not entries:
        print(f"ERROR: no files found under {root}", file=sys.stderr)
        return 2

    docs_dir = BACKEND_ROOT / "docs"
    artifacts_dir = BACKEND_ROOT / "artifacts"
    docs_dir.mkdir(exist_ok=True)
    artifacts_dir.mkdir(exist_ok=True)

    md_path = docs_dir / "dataset-analysis.md"
    md_path.write_text(render_markdown(root, entries), encoding="utf-8")

    json_path = artifacts_dir / "dataset-profile.json"
    json_path.write_text(
        json.dumps({"root": str(root), "files": entries}, indent=2, default=str),
        encoding="utf-8",
    )

    print(f"Scanned {len(entries)} files under {root}")
    print(f"  report  -> {md_path}")
    print(f"  profile -> {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
