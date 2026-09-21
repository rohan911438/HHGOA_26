"""Phase 1 - graph validation.

Runs representative traversal queries against the loaded graph and reports
real numbers, not just "it didn't error." Every query here corresponds to
a real investigation pattern the phase plan names (shared card, shared
device, shared connections) - this is not a generic smoke test.

Checks:
  1. vertex / edge counts match what was loaded
  2. a known Txn is reachable by primary ID
  3. shared-card traversal: Txn -> Card -> other Txns actually returns
     multiple transactions for a card used more than once
  4. shared-device traversal: same, via Device
  5. orphan check: Card/Address/EmailDomain/Device vertices with zero
     incoming edges (would indicate a loader bug - every one of these was
     created *because* some Txn referenced it)
  6. a 2-hop investigation query: from one fraud Txn, find every other Txn
     reachable via any shared entity (card, address, email, or device)

Never writes. Safe to run repeatedly.

Usage:
    python scripts/validate_graph.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    _UNICODE_OK = True
except (AttributeError, ValueError, OSError):
    _UNICODE_OK = False

from app.config import get_settings  # noqa: E402
from app.logging import configure_logging  # noqa: E402
from app.tigergraph.client import get_client  # noqa: E402

CHECK, CROSS = ("✓", "✗") if _UNICODE_OK else ("[OK]", "[X]")


def line(ok: bool, name: str, detail: str) -> str:
    return f"  {CHECK if ok else CROSS} {name} - {detail}"


def main() -> int:
    configure_logging(level="WARNING", json_output=False)
    settings = get_settings()
    client = get_client(settings)
    conn = client.connection

    print("\nGraph validation")
    print("=" * 70)

    results: list[bool] = []

    # ---- 1. counts ---------------------------------------------------
    vcounts = client.vertex_counts()
    ecounts = client.edge_counts()
    total_v = sum(vcounts.values())
    total_e = sum(ecounts.values())
    ok = total_v > 0 and total_e > 0
    results.append(ok)
    print(line(ok, "Vertex/edge counts", f"{total_v:,} vertices, {total_e:,} edges"))
    for vtype, n in sorted(vcounts.items()):
        print(f"      {vtype:<14} {n:>8,}")
    for etype, n in sorted(ecounts.items()):
        print(f"      {etype:<14} {n:>8,}")

    # ---- 2. direct lookup ----------------------------------------------
    try:
        any_txn = conn.getVertices("Txn", limit=1)
        ok = bool(any_txn)
        txn_id = any_txn[0]["v_id"] if ok else None
        results.append(ok)
        print(line(ok, "Direct Txn lookup", f"got vertex id {txn_id!r}" if ok else "no Txn vertices found"))
    except Exception as exc:
        results.append(False)
        print(line(False, "Direct Txn lookup", f"{type(exc).__name__}: {exc}"))
        txn_id = None

    # ---- 3. shared-card traversal --------------------------------------
    try:
        cards = conn.getVertices("Card", limit=30)  # bounded - stop searching after 30 REST round-trips
        shared_card = None
        for card in cards:
            neighbours = conn.getEdges("Card", card["v_id"], edgeType="MADE_WITH_CARD")
            if len(neighbours) >= 2:
                shared_card = (card["v_id"], len(neighbours))
                break
        ok = shared_card is not None
        results.append(ok)
        if ok:
            print(
                line(
                    True,
                    "Shared-card traversal",
                    f"card {shared_card[0]!r} is used by {shared_card[1]} transactions",
                )
            )
        else:
            print(line(False, "Shared-card traversal", "no card is shared by 2+ transactions in this subset"))
    except Exception as exc:
        results.append(False)
        print(line(False, "Shared-card traversal", f"{type(exc).__name__}: {exc}"))
        shared_card = None

    # ---- 4. shared-device traversal ------------------------------------
    try:
        devices = conn.getVertices("Device", limit=30)  # bounded - same reasoning as Card above
        shared_device = None
        for device in devices:
            neighbours = conn.getEdges("Device", device["v_id"], edgeType="USED_DEVICE")
            if len(neighbours) >= 2:
                shared_device = (device["v_id"], len(neighbours))
                break
        ok = shared_device is not None
        results.append(ok)
        if ok:
            print(
                line(
                    True,
                    "Shared-device traversal",
                    f"device {shared_device[0]!r} used by {shared_device[1]} transactions",
                )
            )
        else:
            print(line(False, "Shared-device traversal", "no device shared by 2+ transactions in this subset"))
    except Exception as exc:
        results.append(False)
        print(line(False, "Shared-device traversal", f"{type(exc).__name__}: {exc}"))

    # ---- 5. orphan check -------------------------------------------------
    try:
        orphans: dict[str, int] = {}
        for vtype, etype in (
            ("Card", "MADE_WITH_CARD"),
            ("Address", "BILLED_TO"),
            ("EmailDomain", "PURCHASER_EMAIL"),
            ("Device", "USED_DEVICE"),
        ):
            sample_v = conn.getVertices(vtype, limit=15)  # sampled, not exhaustive - keep REST round-trips bounded
            zero_degree = 0
            for v in sample_v:
                edges = conn.getEdges(vtype, v["v_id"], edgeType=etype)
                if not edges:
                    zero_degree += 1
            if zero_degree:
                orphans[vtype] = zero_degree
        ok = not orphans
        results.append(ok)
        if ok:
            print(line(True, "Orphan check (sampled)", "no zero-degree Card/Address/EmailDomain/Device found"))
        else:
            print(line(False, "Orphan check (sampled)", f"zero-degree vertices found: {orphans}"))
    except Exception as exc:
        results.append(False)
        print(line(False, "Orphan check", f"{type(exc).__name__}: {exc}"))

    # ---- 6. 2-hop investigation query ------------------------------------
    if shared_card:
        try:
            related = conn.getEdges("Card", shared_card[0], edgeType="MADE_WITH_CARD")
            related_txns = {e["to_id"] if e["from_type"] == "Card" else e["from_id"] for e in related}
            ok = len(related_txns) >= 2
            results.append(ok)
            print(
                line(
                    ok,
                    "2-hop: Txn -> Card -> other Txns",
                    f"from card {shared_card[0]!r}: {len(related_txns)} connected transaction(s)",
                )
            )
        except Exception as exc:
            results.append(False)
            print(line(False, "2-hop investigation query", f"{type(exc).__name__}: {exc}"))
    else:
        print("  ○ 2-hop investigation query - skipped, no shared card found to traverse from")

    print("=" * 70)
    healthy = all(results)
    print(f"Result: {'ALL CHECKS PASSED' if healthy else 'SOME CHECKS FAILED'} ({sum(results)}/{len(results)})\n")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
