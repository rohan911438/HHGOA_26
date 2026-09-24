"""Prepare and bulk-load the official HHGOA_IEEE package into TigerGraph.

`prepare()` turns the official CSVs into slim tab-separated load files
(no V1-V339 columns; they are not used by the investigation tools).
`deploy_schema()` + `load()` create the separate `HHGOA_IEEE` graph and
run GSQL loading jobs over those files. The development `HHGOA_FRAUD`
graph is never touched.

Card IDs: the README says card IDs look like `C01234-K1` and are derived
from the issuer fields, but not how. Measured against every labelled
transaction->card pair in the package (closed-case `txn_ids` and the
case pack, 14,975 pairs), `customer_id + "-K" + rank of card6 sorted
alphabetically within the customer` reproduces 100% of them; other
orderings (first-seen, frequency) reproduce ~34%. `derive_card_ids()`
implements the measured rule and `prepare()` re-verifies it on every run.
"""

from __future__ import annotations

import math
import re
import time
from pathlib import Path

from app.benchmark.official import graph_schema
from app.benchmark.official.dataset import DEFAULT_ROOT
from app.tigergraph.client import TigerGraphClient, TigerGraphError

WORK = "_work"
TXN_FILE = "g_txn.tsv"
CLOSED_FILE = "g_closed.tsv"
INVOLVES_FILE = "g_closed_txn.tsv"
CONNECTED_FILE = "g_closed_connected.tsv"

TXN_COLUMNS = [
    "txn_id",
    "ts",
    "amount",
    "product_cd",
    "channel",
    "risk_score",
    "card_id",
    "customer_id",
    "addr1",
    "addr2",
    "dist1",
    "p_email",
    "r_email",
    "m1",
    "m2",
    "m3",
    "m4",
    "m5",
    "m6",
    "m7",
    "m8",
    "m9",
    "device_status",
    "proxy_type",
    "device_type",
    "device_profile",
    "network",
    "card_type",
]


def _is_missing(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _fmt_code(v) -> str:
    """addr1/addr2 are float-typed codes in the CSV ("444.0"); the case
    pack's trigger text uses that same spelling, so keep it."""
    if _is_missing(v):
        return ""
    return f"{float(v):.1f}"


def _clean(v) -> str:
    if _is_missing(v):
        return ""
    return str(v).replace("\t", " ").replace("\n", " ").strip()


def device_profile(info, os_, browser, screen) -> str:
    """README: DeviceProfile = DeviceInfo + OS + browser + screen, shown in
    the answer example as `A | B | C | D`."""
    parts = [_clean(info), _clean(os_), _clean(browser), _clean(screen)]
    return " | ".join(parts) if any(parts) else ""


def derive_card_ids(tx):
    import pandas as pd  # noqa: F401 - pandas frame in, frame out

    c6 = tx["card6"].fillna("NA")
    keys = tx[["customer_id"]].assign(c6=c6).drop_duplicates().sort_values(["customer_id", "c6"])
    keys["k"] = keys.groupby("customer_id").cumcount() + 1
    keys["card_id"] = keys["customer_id"] + "-K" + keys["k"].astype(str)
    return tx.assign(c6=c6).merge(
        keys[["customer_id", "c6", "card_id"]], on=["customer_id", "c6"], how="left"
    )


def read_transactions(root: Path = DEFAULT_ROOT):
    """Official transactions + identity joined, V-columns dropped. Cached
    as parquet under _work/ after the first read."""
    import pandas as pd

    work = root / WORK
    work.mkdir(exist_ok=True)
    cache = work / "tx_joined.parquet"
    if cache.exists() and cache.stat().st_mtime > (root / "transactions.csv").stat().st_mtime:
        return pd.read_parquet(cache)
    hdr = pd.read_csv(root / "transactions.csv", nrows=0).columns
    cols = [c for c in hdr if not (c.startswith("V") and c[1:].isdigit())]
    tx = pd.read_csv(root / "transactions.csv", usecols=cols, engine="pyarrow")
    idn = pd.read_csv(
        root / "identity.csv",
        usecols=[
            "TransactionID",
            "id_15",
            "id_23",
            "id_30",
            "id_31",
            "id_33",
            "DeviceType",
            "DeviceInfo",
        ],
        engine="pyarrow",
    )
    tx = tx.merge(idn, on="TransactionID", how="left")
    tx = derive_card_ids(tx)
    tx["device_profile"] = [
        device_profile(a, b, c, d)
        for a, b, c, d in zip(tx["DeviceInfo"], tx["id_30"], tx["id_31"], tx["id_33"], strict=True)
    ]
    tx["ts"] = pd.to_datetime(tx["ts"])
    tx.to_parquet(cache)
    return tx


def verify_card_rule(tx, root: Path = DEFAULT_ROOT) -> tuple[int, int]:
    import pandas as pd

    cc = pd.read_csv(root / "closed_cases_history.csv")
    cp = pd.read_csv(root / "case_pack.csv")
    pairs = {}
    for r in cc.itertuples():
        for t in str(r.txn_ids).split("|"):
            if t and t != "nan":
                pairs[int(float(t))] = r.card_id
    for r in cp.itertuples():
        pairs[int(r.flagged_txn_id)] = r.card_id
    lookup = dict(zip(tx["TransactionID"], tx["card_id"], strict=True))
    ok = sum(1 for t, c in pairs.items() if lookup.get(t) == c)
    return ok, len(pairs)


def prepare(root: Path = DEFAULT_ROOT) -> dict:
    import pandas as pd

    started = time.perf_counter()
    tx = read_transactions(root)
    ok, total = verify_card_rule(tx, root)
    if ok != total:
        raise RuntimeError(
            f"card_id derivation reproduces only {ok}/{total} labelled pairs - refusing to load"
        )

    out = pd.DataFrame(
        {
            "txn_id": tx["TransactionID"].astype(str),
            "ts": tx["ts"].dt.strftime("%Y-%m-%d %H:%M:%S"),
            "amount": tx["TransactionAmt"].round(2),
            "product_cd": tx["ProductCD"].map(_clean),
            "channel": tx["channel"],
            "risk_score": tx["risk_score"],
            "card_id": tx["card_id"],
            "customer_id": tx["customer_id"],
            "addr1": tx["addr1"].map(_fmt_code),
            "addr2": tx["addr2"].map(_fmt_code),
            "dist1": tx["dist1"].fillna(-1),
            "p_email": tx["P_emaildomain"].map(_clean),
            "r_email": tx["R_emaildomain"].map(_clean),
            **{f"m{i}": tx[f"M{i}"].map(_clean) for i in range(1, 10)},
            "device_status": tx["id_15"].map(_clean),
            "proxy_type": tx["id_23"].map(_clean),
            "device_type": tx["DeviceType"].map(_clean),
            "device_profile": tx["device_profile"],
            "network": tx["card4"].map(_clean),
            "card_type": tx["card6"].map(_clean),
        }
    )[TXN_COLUMNS]
    work = root / WORK
    out.to_csv(work / TXN_FILE, sep="\t", index=False)

    cc = pd.read_csv(root / "closed_cases_history.csv", dtype=str).fillna("")
    closed = cc[CLOSED_COLUMNS].map(_clean)
    closed.to_csv(work / CLOSED_FILE, sep="\t", index=False)
    inv = [(r.case_id, t) for r in cc.itertuples() for t in r.txn_ids.split("|") if t]
    pd.DataFrame(inv, columns=["case_id", "txn_id"]).to_csv(
        work / INVOLVES_FILE, sep="\t", index=False
    )
    con = [(r.case_id, c) for r in cc.itertuples() for c in r.connected_card_ids.split("|") if c]
    pd.DataFrame(con, columns=["case_id", "card_id"]).to_csv(
        work / CONNECTED_FILE, sep="\t", index=False
    )

    return {
        "transactions": len(out),
        "cards": int(out["card_id"].nunique()),
        "customers": int(out["customer_id"].nunique()),
        "device_profiles": int((out["device_profile"] != "").sum()),
        "closed_cases": len(closed),
        "closed_case_txn_links": len(inv),
        "closed_case_connected_cards": len(con),
        "card_rule_verified": f"{ok}/{total}",
        "seconds": round(time.perf_counter() - started, 1),
    }


# ---------------------------------------------------------------- TigerGraph

G = graph_schema.GRAPH_NAME

CLOSED_COLUMNS = [
    "case_id",
    "customer_id",
    "card_id",
    "opened_at",
    "closed_at",
    "outcome",
    "pattern",
    "n_txns",
    "exposure_usd",
    "actions_taken",
    "report_filed",
    "analyst_notes",
]
_USING = 'USING SEPARATOR="\t", HEADER="false", EOL="\n"'


def _positional(template: str, columns: list[str]) -> str:
    """Loading jobs fed by posted data cannot use header names, so `$"name"`
    placeholders are rewritten to `$<index>` from the file's column list."""
    return re.sub(r'\$"(\w+)"', lambda m: f"${columns.index(m.group(1))}", template)


LOAD_TXN_JOB = _positional(
    f"""
CREATE LOADING JOB load_hhgoa_txn FOR GRAPH {G} {{
  DEFINE FILENAME f;
  LOAD f TO VERTEX HTxn VALUES ($"txn_id", $"ts", $"amount", $"product_cd", $"channel", $"risk_score",
        $"card_id", $"customer_id", $"addr1", $"addr2", $"dist1", $"p_email", $"r_email",
        $"m1", $"m2", $"m3", $"m4", $"m5", $"m6", $"m7", $"m8", $"m9",
        $"device_status", $"proxy_type", $"device_type", $"device_profile"),
    TO VERTEX HCustomer VALUES ($"customer_id"),
    TO VERTEX HCard VALUES ($"card_id", $"customer_id", $"network", $"card_type"),
    TO EDGE H_OWNS VALUES ($"customer_id", $"card_id"),
    TO EDGE H_MADE VALUES ($"card_id", $"txn_id"),
    TO VERTEX HDeviceProfile VALUES ($"device_profile") WHERE $"device_profile" != "",
    TO EDGE H_FROM_DEVICE VALUES ($"txn_id", $"device_profile") WHERE $"device_profile" != "",
    TO VERTEX HEmailDomain VALUES ($"p_email") WHERE $"p_email" != "",
    TO EDGE H_PURCHASER_EMAIL VALUES ($"txn_id", $"p_email") WHERE $"p_email" != "",
    TO VERTEX HEmailDomain VALUES ($"r_email") WHERE $"r_email" != "",
    TO EDGE H_RECIPIENT_EMAIL VALUES ($"txn_id", $"r_email") WHERE $"r_email" != "",
    TO VERTEX HBillingRegion VALUES ($"addr1") WHERE $"addr1" != "",
    TO EDGE H_BILLED_IN VALUES ($"txn_id", $"addr1") WHERE $"addr1" != ""
    {_USING};
}}
""".strip(),
    TXN_COLUMNS,
)

LOAD_CLOSED_JOB = _positional(
    f"""
CREATE LOADING JOB load_hhgoa_closed FOR GRAPH {G} {{
  DEFINE FILENAME f;
  LOAD f TO VERTEX HClosedCase VALUES ($"case_id", $"customer_id", $"card_id", $"opened_at", $"closed_at",
        $"outcome", $"pattern", $"n_txns", $"exposure_usd", $"actions_taken", $"report_filed", $"analyst_notes"),
    TO EDGE H_ON_CARD VALUES ($"case_id", $"card_id")
    {_USING};
}}
""".strip(),
    CLOSED_COLUMNS,
)

LOAD_INVOLVES_JOB = f"""
CREATE LOADING JOB load_hhgoa_involves FOR GRAPH {G} {{
  DEFINE FILENAME f;
  LOAD f TO EDGE H_INVOLVES VALUES ($0, $1) {_USING};
}}
""".strip()

LOAD_CONNECTED_JOB = f"""
CREATE LOADING JOB load_hhgoa_connected FOR GRAPH {G} {{
  DEFINE FILENAME f;
  LOAD f TO EDGE H_CONNECTED_TO VALUES ($0, $1) {_USING};
}}
""".strip()

JOBS = [
    ("load_hhgoa_txn", LOAD_TXN_JOB, TXN_FILE),
    ("load_hhgoa_closed", LOAD_CLOSED_JOB, CLOSED_FILE),
    ("load_hhgoa_involves", LOAD_INVOLVES_JOB, INVOLVES_FILE),
    ("load_hhgoa_connected", LOAD_CONNECTED_JOB, CONNECTED_FILE),
]


def official_client() -> TigerGraphClient:
    """Same TigerGraph workspace and credentials as the app, pointed at
    the official graph instead of HHGOA_FRAUD."""
    from app.config import get_settings

    return TigerGraphClient(get_settings().model_copy(update={"tg_graphname": G}))


_GSQL_FAILURE_MARKERS = (
    'Encountered "',
    "Semantic Check Fails",
    "Failed to create",
    "does not exist",
    "error",
)


def _gsql_checked(client: TigerGraphClient, stmt: str) -> str:
    """pyTigerGraph returns some GSQL parse/semantic errors as plain text
    instead of raising - treat those as failures too."""
    out = client.gsql(stmt, allow_write=True).strip()
    if any(m in out for m in _GSQL_FAILURE_MARKERS):
        raise TigerGraphError(f"GSQL rejected statement: {out[-400:]}")
    return out


def deploy_schema(client: TigerGraphClient) -> list[str]:
    """Idempotent: creates only what does not exist yet."""
    log = []
    existing = client.gsql("LS")
    for name, stmt in graph_schema.VERTICES + graph_schema.EDGES:
        if f"- VERTEX {name}(" in existing or f"EDGE {name}(" in existing:
            log.append(f"exists {name}")
            continue
        log.append(f"{name}: {_gsql_checked(client, stmt)[-80:]}")
    if f"Graph {G}(" not in existing:
        log.append(_gsql_checked(client, graph_schema.create_graph_statement())[-120:])
    for name, stmt in [(n, s) for n, s, _ in JOBS]:
        try:
            client.gsql(f"USE GRAPH {G}\nDROP JOB {name}", allow_write=True)
        except TigerGraphError:
            pass  # first deploy: nothing to drop
        log.append(f"{name}: {_gsql_checked(client, f'USE GRAPH {G}' + chr(10) + stmt)[-80:]}")
    return log


TAB = chr(9)
NEWLINE = chr(10)


def _post(conn, lines: list[str], job: str, attempts: int = 5):
    """Upserts are idempotent, so a chunk that hit a transport error
    (measured once: SSLEOFError mid-load) is simply re-posted."""
    for attempt in range(attempts):
        try:
            return conn.runLoadingJobWithData("".join(lines), "f", job, sep=TAB, eol=NEWLINE)
        except (
            OSError,
            ConnectionError,
        ) as exc:  # requests' SSL/connection errors subclass OSError
            if attempt == attempts - 1:
                raise
            print(f"    transient error on {job}, retrying: {type(exc).__name__}", flush=True)
            time.sleep(5 * (attempt + 1))
    return None


def _post_chunks(conn, path: Path, job: str, rows_per_chunk: int, start_row: int = 0) -> list:
    """Posts the file in chunks. `HEADER="true"` is not honoured for posted
    data (measured: the header row was loaded as a data row), so the header
    line is dropped here and the job addresses columns positionally.
    `start_row` resumes an interrupted load (rows before it are skipped)."""
    results = []
    with path.open(encoding="utf-8") as fh:
        fh.readline()
        buf: list[str] = []
        for i, line in enumerate(fh):
            if i < start_row:
                continue
            buf.append(line)
            if len(buf) >= rows_per_chunk:
                results.append(_post(conn, buf, job))
                print(f"    {job}: {i + 1} rows posted", flush=True)
                buf = []
        if buf:
            results.append(_post(conn, buf, job))
    return results


def _stats(results: list) -> dict:
    valid = invalid = 0
    for res in results:
        for item in res or []:
            fl = item.get("statistics", {}).get("parsingStatistics", {}).get("fileLevel", {})
            valid += fl.get("validLine", 0)
            invalid += sum(v for k, v in fl.items() if k != "validLine" and isinstance(v, int))
    return {"valid_lines": valid, "rejected_lines": invalid}


def load(
    client: TigerGraphClient,
    root: Path = DEFAULT_ROOT,
    *,
    rows_per_chunk: int = 40_000,
    resume_txn_row: int = 0,
) -> dict:
    conn = client.connection
    out = {}
    for job, _, fname in JOBS:
        started = time.perf_counter()
        try:
            start = resume_txn_row if job == "load_hhgoa_txn" else 0
            res = _post_chunks(conn, root / WORK / fname, job, rows_per_chunk, start)
        except Exception as exc:  # surfaced to the caller, not swallowed
            raise TigerGraphError(f"loading job {job} failed: {type(exc).__name__}: {exc}") from exc
        out[job] = {
            "chunks": len(res),
            **_stats(res),
            "seconds": round(time.perf_counter() - started, 1),
        }
    return out
