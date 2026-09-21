"""Load the IEEE-CIS development subset into TigerGraph.

Reads data/dev/transactions_dev.csv (+ identity_dev.csv), derives the
vertices and edges defined in docs/tigergraph-schema.md, and upserts them
in batches via pyTigerGraph. Deterministic and idempotent: vertex/edge
upserts are keyed by their natural/composite primary ID, so re-running
this loader against the same subset updates in place rather than
duplicating.

Null handling follows docs/tigergraph-schema.md §4b exactly:
  - nullable numeric attributes -> -1 on a blank source value
  - nullable string attributes  -> "" on a blank source value
  - a missing linking value (e.g. no addr1/addr2 at all) means no edge
    is created, not an edge to a sentinel vertex
"""

from __future__ import annotations

import csv
import hashlib
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from app.logging import get_logger, log_event, InvestigationEvent
from app.tigergraph.client import TigerGraphClient

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
logger = get_logger(__name__)

NUMERIC_D_COLUMNS = [f"D{i}" for i in range(1, 16)]
STRING_M_COLUMNS = [f"M{i}" for i in range(1, 10)]
C_COLUMNS = [f"C{i}" for i in range(1, 15)]


def _num(row: dict[str, str], col: str, default: float = -1.0) -> float:
    v = (row.get(col) or "").strip()
    if not v:
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _str(row: dict[str, str], col: str, default: str = "") -> str:
    v = (row.get(col) or "").strip()
    return v if v else default


def _card_key(row: dict[str, str]) -> str | None:
    parts = [row.get("card1", ""), row.get("card2", ""), row.get("card3", ""), row.get("card5", "")]
    if not parts[0].strip() or not parts[2].strip():
        # card1 and card3 are 0% null in the source; treat a missing one
        # as unusable rather than build a key on absent data.
        return None
    return "|".join(p.strip() for p in parts)


def _addr_key(row: dict[str, str]) -> str | None:
    a1, a2 = row.get("addr1", "").strip(), row.get("addr2", "").strip()
    if not a1 and not a2:
        return None
    return f"{a1}|{a2}"


def _device_key(device_info: str) -> str:
    """Hashed, URL-path-safe primary key for a Device vertex.

    DeviceInfo values are uncontrolled vendor strings and some contain a
    literal "/" (e.g. "SM-A530F Build/NMF26X") - using them directly as a
    TigerGraph primary ID corrupts REST++ URL paths built from the raw ID
    (getEdges and similar reads silently misparse the path; writes via
    POST body happen not to hit this, which is why it wasn't caught at
    load time - see docs/tigergraph-schema.md's Device section). Hashing
    sidesteps guessing which characters are unsafe. The original string
    is preserved verbatim as the `device_info` attribute.
    """
    return hashlib.sha256(device_info.encode("utf-8")).hexdigest()[:16]


@dataclass
class LoadPlan:
    """Vertices and edges staged for upsert, before any network call."""

    vertices: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    edges: dict[str, list[tuple[str, str, dict]]] = field(default_factory=lambda: defaultdict(list))

    def add_vertex(self, vtype: str, vid: str, attrs: dict[str, Any]) -> None:
        # Last-write-wins on duplicate ids within one load, which is fine:
        # Card/Address/EmailDomain/Device attributes are constant per key.
        self.vertices[vtype][vid] = attrs

    def add_edge(self, etype: str, src: str, tgt: str) -> None:
        self.edges[etype].append((src, tgt, {}))

    def counts(self) -> dict[str, int]:
        out = {f"vertex:{k}": len(v) for k, v in self.vertices.items()}
        out.update({f"edge:{k}": len(v) for k, v in self.edges.items()})
        return out


def read_identity_by_txn(identity_path: Path | None) -> dict[str, dict[str, str]]:
    by_txn: dict[str, dict[str, str]] = {}
    if identity_path is None or not identity_path.exists():
        return by_txn
    with identity_path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            tid = (row.get("TransactionID") or "").strip()
            if tid:
                by_txn[tid] = row
    return by_txn


def build_plan(transactions_path: Path, identity_path: Path | None) -> LoadPlan:
    plan = LoadPlan()
    identity_by_txn = read_identity_by_txn(identity_path)

    with transactions_path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            tid = (row.get("TransactionID") or "").strip()
            if not tid:
                continue

            # ---- Txn vertex -------------------------------------------------
            txn_attrs: dict[str, Any] = {
                "is_fraud": _str(row, "isFraud", "0") == "1",
                "transaction_dt": int(_num(row, "TransactionDT", 0)),
                "transaction_amt": _num(row, "TransactionAmt", 0.0),
                "product_cd": _str(row, "ProductCD"),
                "dist1": _num(row, "dist1"),
                "dist2": _num(row, "dist2"),
            }
            for i, col in enumerate(C_COLUMNS, start=1):
                txn_attrs[f"c{i}"] = _num(row, col, 0.0)
            for i, col in enumerate(NUMERIC_D_COLUMNS, start=1):
                txn_attrs[f"d{i}"] = _num(row, col)
            for i, col in enumerate(STRING_M_COLUMNS, start=1):
                txn_attrs[f"m{i}"] = _str(row, col)
            plan.add_vertex("Txn", tid, txn_attrs)

            # ---- Card ---------------------------------------------------
            card_key = _card_key(row)
            if card_key:
                plan.add_vertex(
                    "Card",
                    card_key,
                    {
                        "card1": _str(row, "card1"),
                        "card2": _str(row, "card2"),
                        "card3": _str(row, "card3"),
                        "card5": _str(row, "card5"),
                        "card_network": _str(row, "card4"),
                        "card_type": _str(row, "card6"),
                    },
                )
                plan.add_edge("MADE_WITH_CARD", tid, card_key)

            # ---- Address --------------------------------------------------
            addr_key = _addr_key(row)
            if addr_key:
                plan.add_vertex(
                    "Address", addr_key, {"addr1": _str(row, "addr1"), "addr2": _str(row, "addr2")}
                )
                plan.add_edge("BILLED_TO", tid, addr_key)

            # ---- EmailDomain (purchaser + recipient) -----------------------
            p_domain = _str(row, "P_emaildomain")
            if p_domain:
                plan.add_vertex("EmailDomain", p_domain, {})
                plan.add_edge("PURCHASER_EMAIL", tid, p_domain)

            r_domain = _str(row, "R_emaildomain")
            if r_domain:
                plan.add_vertex("EmailDomain", r_domain, {})
                plan.add_edge("RECIPIENT_EMAIL", tid, r_domain)

            # ---- Device (via identity-file join) --------------------------
            identity_row = identity_by_txn.get(tid)
            if identity_row:
                device_info = _str(identity_row, "DeviceInfo")
                if device_info:
                    device_key = _device_key(device_info)
                    plan.add_vertex(
                        "Device",
                        device_key,
                        {"device_info": device_info, "device_type": _str(identity_row, "DeviceType")},
                    )
                    plan.add_edge("USED_DEVICE", tid, device_key)

    return plan


def _chunks(seq: list, size: int) -> Iterator[list]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def load_plan(client: TigerGraphClient, plan: LoadPlan, *, batch_size: int = 1000) -> dict[str, Any]:
    """Upsert a LoadPlan. Returns a report of what was accepted."""
    conn = client.connection
    report: dict[str, Any] = {"vertices": {}, "edges": {}, "errors": []}

    # Vertices first - edges reference them.
    for vtype, items in plan.vertices.items():
        pairs = list(items.items())
        accepted = 0
        for batch in _chunks(pairs, batch_size):
            try:
                accepted += conn.upsertVertices(vtype, batch)
            except Exception as exc:
                msg = f"{vtype}: {type(exc).__name__}: {exc}"
                report["errors"].append(msg)
                log_event(logger, InvestigationEvent.TOOL_FAILED, msg)
        report["vertices"][vtype] = {"attempted": len(pairs), "accepted": accepted}

    edge_endpoints = {
        "MADE_WITH_CARD": ("Txn", "Card"),
        "BILLED_TO": ("Txn", "Address"),
        "PURCHASER_EMAIL": ("Txn", "EmailDomain"),
        "RECIPIENT_EMAIL": ("Txn", "EmailDomain"),
        "USED_DEVICE": ("Txn", "Device"),
    }
    for etype, items in plan.edges.items():
        src_type, tgt_type = edge_endpoints[etype]
        accepted = 0
        for batch in _chunks(items, batch_size):
            try:
                accepted += conn.upsertEdges(src_type, etype, tgt_type, batch)
            except Exception as exc:
                msg = f"{etype}: {type(exc).__name__}: {exc}"
                report["errors"].append(msg)
                log_event(logger, InvestigationEvent.TOOL_FAILED, msg)
        report["edges"][etype] = {"attempted": len(items), "accepted": accepted}

    return report
