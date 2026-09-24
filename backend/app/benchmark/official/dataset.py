"""Official HHGOA_IEEE package: locate, fetch, and validate.

Everything here is derived from the package's own README.md (read in
full before this module was written). Nothing falls back to the Kaggle
IEEE-CIS development copy under data/hhgoa/ - if the official package is
missing or malformed, `validate_official_package()` reports it and
`load_case_pack()` raises `OfficialDatasetUnavailable`.
"""

from __future__ import annotations

import csv
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from app.config import BACKEND_ROOT

DATASET_NAME = "HHGOA_IEEE"

# The Drive folder linked from TigerGraph's challenge brief
# ("TigerGraph Agentic Fraud Investigation HHGOA", Dataset section).
OFFICIAL_DRIVE_FOLDER_ID = "1YDJUW1fiE7Jx8R9KqknC4IcsED9zll2A"

DEFAULT_ROOT = BACKEND_ROOT / "data" / "hhgoa_ieee"

# README "Files in this folder".
REQUIRED_FILES = (
    "README.md",
    "transactions.csv",
    "identity.csv",
    "closed_cases_history.csv",
    "case_pack.csv",
)

# README "The case pack" section.
CASE_PACK_COLUMNS = (
    "case_id",
    "opened_at",
    "trigger_type",
    "trigger_text",
    "flagged_txn_id",
    "card_id",
    "customer_id",
    "risk_score",
)
TRIGGER_TYPES = {"risk_score", "customer_report", "analyst_request"}

# README "closed_cases_history.csv" section.
CLOSED_CASE_COLUMNS = (
    "case_id",
    "customer_id",
    "card_id",
    "opened_at",
    "closed_at",
    "outcome",
    "pattern",
    "first_fraud_txn_id",
    "txn_ids",
    "n_txns",
    "exposure_usd",
    "connected_card_ids",
    "actions_taken",
    "report_filed",
    "analyst_notes",
)

# README "The columns we added".
ADDED_TXN_COLUMNS = ("customer_id", "ts", "channel", "risk_score")

# README headline counts. Checked, not assumed.
EXPECTED_CASE_COUNT = 20
EXPECTED_TXN_ROWS = 590_742
EXPECTED_IDENTITY_ROWS = 144_432
EXPECTED_CLOSED_CASES = 5_565

# README "Answer Format" - these sections must exist for us to build
# answers against them.
README_REQUIRED_SECTIONS = (
    "# Fraud Policy",
    "# Answer Format",
    "## The five known fraud patterns",
    "## Regulatory references",
    "# The 20 Cases",
)

CASE_ID_RE = re.compile(r"^HHG-\d{3}$")
CARD_ID_RE = re.compile(r"^C\d{5}-K\d+$")
CUSTOMER_ID_RE = re.compile(r"^C\d{5}$")


class OfficialDatasetUnavailable(RuntimeError):
    """The official package is missing or invalid. Callers must not
    substitute the IEEE-CIS development data."""


@dataclass(frozen=True)
class OfficialCase:
    case_id: str
    opened_at: str
    trigger_type: str
    trigger_text: str
    flagged_txn_id: str
    card_id: str
    customer_id: str
    risk_score: float | None
    is_official_benchmark_case: bool = True


@dataclass
class ValidationReport:
    dataset: str = DATASET_NAME
    root: str = ""
    official: bool = False
    checks: list[dict] = field(default_factory=list)
    cases: list[dict] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        return ok

    @property
    def ok(self) -> bool:
        return all(c["ok"] for c in self.checks)

    def as_manifest(self) -> dict:
        return {
            "dataset": self.dataset,
            "official": self.official and self.ok,
            "source": f"https://drive.google.com/drive/folders/{OFFICIAL_DRIVE_FOLDER_ID}",
            "root": self.root,
            "case_count": len(self.cases),
            "valid": self.ok,
            "checks": self.checks,
            "cases": self.cases,
        }


def _count_rows(path: Path) -> int:
    with path.open("rb") as fh:
        return sum(1 for _ in fh) - 1


def _header(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as fh:
        return next(csv.reader(fh))


def read_case_pack(root: Path = DEFAULT_ROOT) -> list[OfficialCase]:
    path = root / "case_pack.csv"
    if not path.exists():
        raise OfficialDatasetUnavailable(
            f"ERROR: OFFICIAL HHGOA DATASET UNAVAILABLE - {path} not found. "
            "Run `python -m app.benchmark.official fetch`."
        )
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return [
        OfficialCase(
            case_id=r["case_id"].strip(),
            opened_at=r["opened_at"].strip(),
            trigger_type=r["trigger_type"].strip(),
            trigger_text=r["trigger_text"].strip(),
            flagged_txn_id=r["flagged_txn_id"].strip(),
            card_id=r["card_id"].strip(),
            customer_id=r["customer_id"].strip(),
            risk_score=float(r["risk_score"]) if r["risk_score"].strip() else None,
        )
        for r in rows
    ]


def validate_official_package(
    root: Path = DEFAULT_ROOT, *, count_rows: bool = True
) -> ValidationReport:
    """Checks the package against its own README. Read-only."""
    rep = ValidationReport(root=str(root))
    if not rep.check("dataset_root_exists", root.exists(), str(root)):
        return rep

    missing = [f for f in REQUIRED_FILES if not (root / f).exists()]
    if not rep.check(
        "required_files_present", not missing, f"missing: {missing}" if missing else "all 5"
    ):
        return rep

    readme = (root / "README.md").read_text(encoding="utf-8")
    rep.check("readme_identifies_dataset", "IEEE-CIS" in readme and "Hacker House Goa" in readme)
    absent = [s for s in README_REQUIRED_SECTIONS if s not in readme]
    rep.check(
        "readme_policy_patterns_answer_format",
        not absent,
        f"missing sections: {absent}" if absent else "",
    )

    rep.check("case_pack_header", _header(root / "case_pack.csv") == list(CASE_PACK_COLUMNS))
    rep.check(
        "closed_cases_header",
        _header(root / "closed_cases_history.csv") == list(CLOSED_CASE_COLUMNS),
    )
    txn_header = _header(root / "transactions.csv")
    rep.check("transactions_added_columns", all(c in txn_header for c in ADDED_TXN_COLUMNS))
    rep.check(
        "transactions_no_fraud_label",
        "isFraud" not in txn_header,
        "README: 'the yes/no fraud label is gone'",
    )
    rep.check(
        "identity_joins_on_TransactionID", _header(root / "identity.csv")[0] == "TransactionID"
    )

    cases = read_case_pack(root)
    ids = [c.case_id for c in cases]
    rep.check("case_count_is_20", len(cases) == EXPECTED_CASE_COUNT, f"found {len(cases)}")
    rep.check("case_ids_unique", len(set(ids)) == len(ids))
    rep.check("case_ids_well_formed", all(CASE_ID_RE.match(i) for i in ids))
    rep.check("flagged_txns_unique", len({c.flagged_txn_id for c in cases}) == len(cases))
    rep.check("trigger_types_known", all(c.trigger_type in TRIGGER_TYPES for c in cases))
    rep.check("card_ids_well_formed", all(CARD_ID_RE.match(c.card_id) for c in cases))
    rep.check(
        "card_belongs_to_customer", all(c.card_id.startswith(c.customer_id + "-") for c in cases)
    )
    rep.check(
        "risk_score_only_for_risk_triggers",
        all((c.risk_score is not None) == (c.trigger_type == "risk_score") for c in cases),
    )
    # The README repeats the case table; the CSV must agree with it.
    rep.check("case_pack_matches_readme_table", all(f"| {i} |" in readme for i in ids))

    # Every flagged transaction must exist in transactions.csv with the
    # stated customer (README Rules: "Every ID in your answer files must
    # exist in this dataset").
    wanted = {c.flagged_txn_id: c for c in cases}
    found: dict[str, str] = {}
    txn_rows = 0
    with (root / "transactions.csv").open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        hdr = next(reader)
        i_id, i_cust = hdr.index("TransactionID"), hdr.index("customer_id")
        for row in reader:
            txn_rows += 1
            if row[i_id] in wanted:
                found[row[i_id]] = row[i_cust]
            if not count_rows and len(found) == len(wanted):
                break
    missing_txn = sorted(set(wanted) - set(found))
    rep.check(
        "flagged_txns_exist", not missing_txn, f"missing: {missing_txn}" if missing_txn else "20/20"
    )
    wrong_cust = [t for t, cu in found.items() if cu != wanted[t].customer_id]
    rep.check(
        "flagged_txn_customer_matches",
        not wrong_cust,
        f"mismatch: {wrong_cust}" if wrong_cust else "",
    )

    if count_rows:
        rep.check("transactions_row_count", txn_rows == EXPECTED_TXN_ROWS, f"{txn_rows}")
        n_id = _count_rows(root / "identity.csv")
        rep.check("identity_row_count", n_id == EXPECTED_IDENTITY_ROWS, f"{n_id}")
    n_cc = _count_rows(root / "closed_cases_history.csv")
    rep.check("closed_case_row_count", n_cc == EXPECTED_CLOSED_CASES, f"{n_cc}")

    rep.cases = [
        {
            "case_id": c.case_id,
            "transaction_id": c.flagged_txn_id,
            "card_id": c.card_id,
            "customer_id": c.customer_id,
            "trigger_type": c.trigger_type,
            "source": "official",
            "is_official_benchmark_case": True,
        }
        for c in cases
    ]
    rep.official = True
    return rep


def load_official_cases(root: Path = DEFAULT_ROOT) -> list[OfficialCase]:
    """The 20 official cases, or `OfficialDatasetUnavailable`. Never the
    development fallback."""
    rep = validate_official_package(root, count_rows=False)
    if not rep.ok:
        failed = [c for c in rep.checks if not c["ok"]]
        raise OfficialDatasetUnavailable(
            f"ERROR: OFFICIAL HHGOA DATASET UNAVAILABLE or invalid at {root}: {failed}"
        )
    return read_case_pack(root)


# ---------------------------------------------------------------- fetch

_ENTRY_RE = re.compile(
    r'href="https://drive\.google\.com/file/d/([\w-]+)/view[^"]*".*?flip-entry-title">([^<]+)<',
    re.DOTALL,
)


def fetch_official_package(root: Path = DEFAULT_ROOT) -> list[str]:
    """Downloads the five files from TigerGraph's public Drive folder.
    Only this folder ID is ever contacted - no mirrors."""
    root.mkdir(parents=True, exist_ok=True)
    listing_url = f"https://drive.google.com/embeddedfolderview?id={OFFICIAL_DRIVE_FOLDER_ID}"
    with urllib.request.urlopen(listing_url, timeout=60) as resp:
        html = resp.read().decode("utf-8", "replace")
    entries = {name.strip(): fid for fid, name in _ENTRY_RE.findall(html)}
    missing = [f for f in REQUIRED_FILES if f not in entries]
    if missing:
        raise OfficialDatasetUnavailable(f"Official Drive folder listing lacks {missing}")
    fetched = []
    for name in REQUIRED_FILES:
        url = f"https://drive.usercontent.google.com/download?id={entries[name]}&export=download&confirm=t"
        urllib.request.urlretrieve(url, root / name)
        fetched.append(name)
    return fetched
