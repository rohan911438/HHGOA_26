"""Phase 2A - TigerGraph investigation queries.

Read-only GSQL queries answering the minimum useful fraud-investigation
questions the graph can actually answer, given the real, deployed schema
(see docs/tigergraph-schema.md): a transaction's own attributes, and
everything reachable through each of its four linking entities (Card,
Address, EmailDomain via two edge types, Device).

Design choices, and why:

  * Queries run as GSQL INTERPRETED queries (`runInterpretedQuery`), not
    installed/compiled queries. Installing a query took ~30-60s live
    against this Savanna instance during Phase 1's schema deployment -
    fine for a one-time schema, too slow for iterating on ~6 queries.
    Interpreted queries execute immediately with no install step, at
    some per-call performance cost. Revisit: `scripts/install_queries.py`
    (not built yet) can promote the ones actually used in the hot path
    to installed queries once Phase 2's tool surface stabilizes.

  * Each query takes a typed `VERTEX<Txn>` parameter (the transaction
    itself), not a raw ID string plus a WHERE-based lookup - simpler,
    and `pyTigerGraph.runInterpretedQuery` accepts the primary ID
    directly for a typed vertex parameter (confirmed against the live
    server; the untyped-VERTEX tuple form documented elsewhere does not
    apply here and produced a real, caught error before this was fixed).

  * Related-transaction results are trimmed client-side to a small,
    named set of fields (id, is_fraud, amount, datetime-offset, product
    code) rather than the full ~46-attribute Txn row GSQL returns by
    default. Keeps payloads small and keeps "what evidence did this
    query actually surface" legible, without adding GSQL attribute-
    projection syntax risk on top of everything else already proven
    live in this module.

  * No evidence scoring, confidence, or signal-strength values here -
    that is Phase 2B's job. This layer returns facts (counts, IDs,
    attributes), not judgments. The one exception is the real,
    measured observation documented on `investigate_transaction_network`
    below, which is a fact about this dataset's graph structure, not a
    scoring decision.

Real, measured finding worth carrying into Phase 2B: shared `Address` is
a much noisier linking signal than shared `Card` or `Device` in this
dataset. For one live test transaction, a shared `Card` linked to 12
other transactions and a shared purchaser `EmailDomain` to 20, while its
shared `Address` alone linked to 312 - `addr1`/`addr2` are coarse
region-level codes (see docs/dataset-analysis.md), so many genuinely
unrelated transactions share one. Any future evidence-strength model
should weight these differently, not treat "shared entity" as one
undifferentiated signal.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import requests

from app.logging import InvestigationEvent, get_logger, log_event
from app.tigergraph.client import TigerGraphClient, TigerGraphError, TigerGraphQueryError

logger = get_logger(__name__)

# Fields kept for a "related transaction" result - enough to reason about
# without echoing the full opaque C/D/M/V-derived attribute set.
_TXN_SUMMARY_FIELDS = ("is_fraud", "transaction_amt", "transaction_dt", "product_cd")

# Transport-level retry for the read-only interpreted queries (see _run).
_TRANSIENT_ATTEMPTS = 3
_TRANSIENT_BACKOFF_SECONDS = 1.0
_TRANSIENT_ERRORS = (requests.exceptions.ConnectionError, ConnectionError)


def _txn_summary(v: dict[str, Any]) -> dict[str, Any]:
    attrs = v.get("attributes", {})
    return {"transaction_id": v.get("v_id"), **{f: attrs.get(f) for f in _TXN_SUMMARY_FIELDS}}


def _entity_summary(v: dict[str, Any]) -> dict[str, Any] | None:
    if not v:
        return None
    return {"id": v.get("v_id"), "type": v.get("v_type"), **v.get("attributes", {})}


# ---------------------------------------------------------------- results


@dataclass
class TransactionContext:
    """A transaction's own attributes plus its direct (1-hop) neighborhood."""

    transaction_id: str
    attributes: dict[str, Any]
    card: dict[str, Any] | None
    address: dict[str, Any] | None
    purchaser_email: dict[str, Any] | None
    recipient_email: dict[str, Any] | None
    device: dict[str, Any] | None


@dataclass
class SharedEntityActivity:
    """Other transactions reachable through one shared linking entity."""

    entity_type: str  # "Card" | "Address" | "Device"
    seed_transaction_id: str
    entity: dict[str, Any] | None
    related_transactions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def related_count(self) -> int:
        return len(self.related_transactions)


@dataclass
class SharedEmailActivity:
    """Other transactions reachable through a shared purchaser or recipient
    email domain. Kept separate from SharedEntityActivity because a
    transaction has two distinct email-linkage edges to the same vertex
    type, not one."""

    seed_transaction_id: str
    purchaser_domain: dict[str, Any] | None
    via_purchaser_domain: list[dict[str, Any]] = field(default_factory=list)
    recipient_domain: dict[str, Any] | None = None
    via_recipient_domain: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class NetworkSummary:
    """2-hop fan-out from a transaction, broken down by which linking
    entity type formed the connection - see the module docstring's
    finding on why the breakdown matters more than a combined total."""

    seed_transaction_id: str
    linked_entities: dict[str, int]  # entity type -> count directly linked
    related_by_entity_type: dict[str, int]  # entity type -> related txn count via that type

    @property
    def total_related(self) -> int:
        """Not a deduplicated count - the same related transaction can be
        reached via more than one entity type, and this sum does not
        collapse that. Treat as an upper bound on distinct related
        transactions, not an exact count."""
        return sum(self.related_by_entity_type.values())


# ---------------------------------------------------------------- queries


_Q_TRANSACTION_CONTEXT = """
INTERPRET QUERY (VERTEX<Txn> seed) FOR GRAPH HHGOA_FRAUD {
  Seed = {seed};
  Cards = SELECT c FROM Seed:s -(MADE_WITH_CARD:e)- Card:c;
  Addrs = SELECT a FROM Seed:s -(BILLED_TO:e)- Address:a;
  PEmails = SELECT d FROM Seed:s -(PURCHASER_EMAIL:e)- EmailDomain:d;
  REmails = SELECT d FROM Seed:s -(RECIPIENT_EMAIL:e)- EmailDomain:d;
  Devices = SELECT dv FROM Seed:s -(USED_DEVICE:e)- Device:dv;
  PRINT Seed;
  PRINT Cards;
  PRINT Addrs;
  PRINT PEmails;
  PRINT REmails;
  PRINT Devices;
}
"""

_Q_SHARED_CARD = """
INTERPRET QUERY (VERTEX<Txn> seed) FOR GRAPH HHGOA_FRAUD {
  Seed = {seed};
  MyCard = SELECT c FROM Seed:s -(MADE_WITH_CARD:e)- Card:c;
  Related = SELECT t FROM MyCard:c -(MADE_WITH_CARD:e)- Txn:t WHERE t != seed;
  PRINT MyCard;
  PRINT Related;
}
"""

_Q_SHARED_ADDRESS = """
INTERPRET QUERY (VERTEX<Txn> seed) FOR GRAPH HHGOA_FRAUD {
  Seed = {seed};
  MyAddr = SELECT a FROM Seed:s -(BILLED_TO:e)- Address:a;
  Related = SELECT t FROM MyAddr:a -(BILLED_TO:e)- Txn:t WHERE t != seed;
  PRINT MyAddr;
  PRINT Related;
}
"""

_Q_SHARED_DEVICE = """
INTERPRET QUERY (VERTEX<Txn> seed) FOR GRAPH HHGOA_FRAUD {
  Seed = {seed};
  MyDevice = SELECT dv FROM Seed:s -(USED_DEVICE:e)- Device:dv;
  Related = SELECT t FROM MyDevice:dv -(USED_DEVICE:e)- Txn:t WHERE t != seed;
  PRINT MyDevice;
  PRINT Related;
}
"""

_Q_SHARED_EMAIL = """
INTERPRET QUERY (VERTEX<Txn> seed) FOR GRAPH HHGOA_FRAUD {
  Seed = {seed};
  MyPEmail = SELECT d FROM Seed:s -(PURCHASER_EMAIL:e)- EmailDomain:d;
  MyREmail = SELECT d FROM Seed:s -(RECIPIENT_EMAIL:e)- EmailDomain:d;
  ViaPurchaser = SELECT t FROM MyPEmail:d -(PURCHASER_EMAIL:e)- Txn:t WHERE t != seed;
  ViaRecipient = SELECT t FROM MyREmail:d -(RECIPIENT_EMAIL:e)- Txn:t WHERE t != seed;
  PRINT MyPEmail;
  PRINT MyREmail;
  PRINT ViaPurchaser;
  PRINT ViaRecipient;
}
"""

_Q_NETWORK_SUMMARY = """
INTERPRET QUERY (VERTEX<Txn> seed) FOR GRAPH HHGOA_FRAUD {
  Seed = {seed};
  Cards = SELECT c FROM Seed:s -(MADE_WITH_CARD:e)- Card:c;
  Addrs = SELECT a FROM Seed:s -(BILLED_TO:e)- Address:a;
  PEmails = SELECT d FROM Seed:s -(PURCHASER_EMAIL:e)- EmailDomain:d;
  REmails = SELECT d FROM Seed:s -(RECIPIENT_EMAIL:e)- EmailDomain:d;
  Devices = SELECT dv FROM Seed:s -(USED_DEVICE:e)- Device:dv;
  ViaCard = SELECT t FROM Cards:c -(MADE_WITH_CARD:e)- Txn:t WHERE t != seed;
  ViaAddr = SELECT t FROM Addrs:a -(BILLED_TO:e)- Txn:t WHERE t != seed;
  ViaPEmail = SELECT t FROM PEmails:d -(PURCHASER_EMAIL:e)- Txn:t WHERE t != seed;
  ViaREmail = SELECT t FROM REmails:d -(RECIPIENT_EMAIL:e)- Txn:t WHERE t != seed;
  ViaDevice = SELECT t FROM Devices:dv -(USED_DEVICE:e)- Txn:t WHERE t != seed;
  PRINT Cards.size() AS n_cards, Addrs.size() AS n_addrs, PEmails.size() AS n_pemails,
        REmails.size() AS n_remails, Devices.size() AS n_devices;
  PRINT ViaCard.size() AS via_card, ViaAddr.size() AS via_addr, ViaPEmail.size() AS via_pemail,
        ViaREmail.size() AS via_remail, ViaDevice.size() AS via_device;
}
"""


def _run(client: TigerGraphClient, name: str, gsql: str, seed_txn_id: str) -> list[dict]:
    log_event(logger, InvestigationEvent.GRAPH_QUERY, f"running {name}", query=name, seed=seed_txn_id)
    # Every query here is a read-only INTERPRET query, so a transport-level
    # failure (DNS lookup miss, connection reset) is safe to retry. Query
    # errors and read timeouts are not retried - they fail exactly as before.
    for attempt in range(1, _TRANSIENT_ATTEMPTS + 1):
        try:
            result = client.connection.runInterpretedQuery(gsql, params={"seed": seed_txn_id})
            break
        except _TRANSIENT_ERRORS as exc:
            if attempt == _TRANSIENT_ATTEMPTS:
                log_event(
                    logger,
                    InvestigationEvent.TOOL_FAILED,
                    f"{name} failed",
                    query=name,
                    error=str(exc),
                )
                raise TigerGraphQueryError(f"{name} failed: {type(exc).__name__}: {exc}") from exc
            log_event(
                logger,
                InvestigationEvent.TOOL_FAILED,
                f"{name} transient connection error, retrying",
                query=name,
                attempt=attempt,
                error=str(exc),
            )
            time.sleep(_TRANSIENT_BACKOFF_SECONDS * attempt)
        except Exception as exc:
            log_event(
                logger, InvestigationEvent.TOOL_FAILED, f"{name} failed", query=name, error=str(exc)
            )
            raise TigerGraphQueryError(f"{name} failed: {type(exc).__name__}: {exc}") from exc
    log_event(logger, InvestigationEvent.GRAPH_RESULT, f"{name} returned", query=name)
    return result


def _find_block(result: list[dict], key: str) -> Any:
    for block in result:
        if key in block:
            return block[key]
    return None


def get_transaction_context(client: TigerGraphClient, transaction_id: str) -> TransactionContext:
    """A transaction's own attributes plus everything directly linked to it."""
    result = _run(client, "get_transaction_context", _Q_TRANSACTION_CONTEXT, transaction_id)

    seed_rows = _find_block(result, "Seed") or []
    if not seed_rows:
        raise TigerGraphQueryError(f"transaction '{transaction_id}' does not exist")
    seed = seed_rows[0]

    def first_entity(key: str) -> dict[str, Any] | None:
        rows = _find_block(result, key) or []
        return _entity_summary(rows[0]) if rows else None

    return TransactionContext(
        transaction_id=transaction_id,
        attributes=seed.get("attributes", {}),
        card=first_entity("Cards"),
        address=first_entity("Addrs"),
        purchaser_email=first_entity("PEmails"),
        recipient_email=first_entity("REmails"),
        device=first_entity("Devices"),
    )


def find_shared_card_activity(client: TigerGraphClient, transaction_id: str) -> SharedEntityActivity:
    result = _run(client, "find_shared_card_activity", _Q_SHARED_CARD, transaction_id)
    card_rows = _find_block(result, "MyCard") or []
    related_rows = _find_block(result, "Related") or []
    return SharedEntityActivity(
        entity_type="Card",
        seed_transaction_id=transaction_id,
        entity=_entity_summary(card_rows[0]) if card_rows else None,
        related_transactions=[_txn_summary(r) for r in related_rows],
    )


def find_shared_address_activity(client: TigerGraphClient, transaction_id: str) -> SharedEntityActivity:
    result = _run(client, "find_shared_address_activity", _Q_SHARED_ADDRESS, transaction_id)
    addr_rows = _find_block(result, "MyAddr") or []
    related_rows = _find_block(result, "Related") or []
    return SharedEntityActivity(
        entity_type="Address",
        seed_transaction_id=transaction_id,
        entity=_entity_summary(addr_rows[0]) if addr_rows else None,
        related_transactions=[_txn_summary(r) for r in related_rows],
    )


def find_shared_device_activity(client: TigerGraphClient, transaction_id: str) -> SharedEntityActivity:
    result = _run(client, "find_shared_device_activity", _Q_SHARED_DEVICE, transaction_id)
    device_rows = _find_block(result, "MyDevice") or []
    related_rows = _find_block(result, "Related") or []
    return SharedEntityActivity(
        entity_type="Device",
        seed_transaction_id=transaction_id,
        entity=_entity_summary(device_rows[0]) if device_rows else None,
        related_transactions=[_txn_summary(r) for r in related_rows],
    )


def find_shared_email_activity(client: TigerGraphClient, transaction_id: str) -> SharedEmailActivity:
    result = _run(client, "find_shared_email_activity", _Q_SHARED_EMAIL, transaction_id)
    p_rows = _find_block(result, "MyPEmail") or []
    r_rows = _find_block(result, "MyREmail") or []
    via_p = _find_block(result, "ViaPurchaser") or []
    via_r = _find_block(result, "ViaRecipient") or []
    return SharedEmailActivity(
        seed_transaction_id=transaction_id,
        purchaser_domain=_entity_summary(p_rows[0]) if p_rows else None,
        via_purchaser_domain=[_txn_summary(r) for r in via_p],
        recipient_domain=_entity_summary(r_rows[0]) if r_rows else None,
        via_recipient_domain=[_txn_summary(r) for r in via_r],
    )


def investigate_transaction_network(client: TigerGraphClient, transaction_id: str) -> NetworkSummary:
    """2-hop fan-out, broken down by linking entity type.

    Counts-only by design (not the related transaction IDs themselves) -
    the per-entity-type queries above already provide the actual related
    transactions when a specific linkage type is what an investigation
    needs to follow up on. This query answers "which linkage type
    matters here", not "give me everything".
    """
    result = _run(client, "investigate_transaction_network", _Q_NETWORK_SUMMARY, transaction_id)
    linked_block = result[0] if len(result) > 0 else {}
    related_block = result[1] if len(result) > 1 else {}
    return NetworkSummary(
        seed_transaction_id=transaction_id,
        linked_entities={
            "Card": linked_block.get("n_cards", 0),
            "Address": linked_block.get("n_addrs", 0),
            "EmailDomain_purchaser": linked_block.get("n_pemails", 0),
            "EmailDomain_recipient": linked_block.get("n_remails", 0),
            "Device": linked_block.get("n_devices", 0),
        },
        related_by_entity_type={
            "Card": related_block.get("via_card", 0),
            "Address": related_block.get("via_addr", 0),
            "EmailDomain_purchaser": related_block.get("via_pemail", 0),
            "EmailDomain_recipient": related_block.get("via_remail", 0),
            "Device": related_block.get("via_device", 0),
        },
    )
