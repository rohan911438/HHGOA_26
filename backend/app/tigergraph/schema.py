"""GSQL schema definition for HHGOA_FRAUD.

Generates the exact GSQL from docs/tigergraph-schema.md - kept as data
here (not string-templated inline in the deploy script) so the schema
definition has one home and `scripts/create_schema.py` stays a thin,
reviewable deployment wrapper.

Every attribute name, type and nullability decision here traces to
docs/tigergraph-schema.md - nothing invented. See that document's §4b for
the null-handling convention (numeric missing -> -1, string missing ->
"").
"""

from __future__ import annotations

GRAPH_NAME = "HHGOA_FRAUD"

# ---------------------------------------------------------------- vertices

VERTEX_TRANSACTION = """
CREATE VERTEX Txn (
    PRIMARY_ID transaction_id STRING,
    is_fraud BOOL,
    transaction_dt INT,
    transaction_amt DOUBLE,
    product_cd STRING,
    dist1 DOUBLE DEFAULT -1,
    dist2 DOUBLE DEFAULT -1,
    c1 DOUBLE, c2 DOUBLE, c3 DOUBLE, c4 DOUBLE, c5 DOUBLE, c6 DOUBLE,
    c7 DOUBLE, c8 DOUBLE, c9 DOUBLE, c10 DOUBLE, c11 DOUBLE, c12 DOUBLE,
    c13 DOUBLE, c14 DOUBLE,
    d1 DOUBLE DEFAULT -1, d2 DOUBLE DEFAULT -1, d3 DOUBLE DEFAULT -1,
    d4 DOUBLE DEFAULT -1, d5 DOUBLE DEFAULT -1, d6 DOUBLE DEFAULT -1,
    d7 DOUBLE DEFAULT -1, d8 DOUBLE DEFAULT -1, d9 DOUBLE DEFAULT -1,
    d10 DOUBLE DEFAULT -1, d11 DOUBLE DEFAULT -1, d12 DOUBLE DEFAULT -1,
    d13 DOUBLE DEFAULT -1, d14 DOUBLE DEFAULT -1, d15 DOUBLE DEFAULT -1,
    m1 STRING DEFAULT "", m2 STRING DEFAULT "", m3 STRING DEFAULT "",
    m4 STRING DEFAULT "", m5 STRING DEFAULT "", m6 STRING DEFAULT "",
    m7 STRING DEFAULT "", m8 STRING DEFAULT "", m9 STRING DEFAULT ""
) WITH STATS="OUTDEGREE_BY_EDGETYPE"
""".strip()

VERTEX_CARD = """
CREATE VERTEX Card (
    PRIMARY_ID card_key STRING,
    card1 STRING,
    card2 STRING DEFAULT "",
    card3 STRING,
    card5 STRING DEFAULT "",
    card_network STRING,
    card_type STRING
)
""".strip()

VERTEX_ADDRESS = """
CREATE VERTEX Address (
    PRIMARY_ID address_key STRING,
    addr1 STRING DEFAULT "",
    addr2 STRING DEFAULT ""
)
""".strip()

VERTEX_EMAIL_DOMAIN = """
CREATE VERTEX EmailDomain (
    PRIMARY_ID domain STRING
)
""".strip()

VERTEX_DEVICE = """
CREATE VERTEX Device (
    PRIMARY_ID device_key STRING,
    device_info STRING,
    device_type STRING DEFAULT ""
)
""".strip()

VERTICES = [
    ("Txn", VERTEX_TRANSACTION),
    ("Card", VERTEX_CARD),
    ("Address", VERTEX_ADDRESS),
    ("EmailDomain", VERTEX_EMAIL_DOMAIN),
    ("Device", VERTEX_DEVICE),
]

# ------------------------------------------------------------------ edges

EDGE_MADE_WITH_CARD = """
CREATE UNDIRECTED EDGE MADE_WITH_CARD (
    FROM Txn, TO Card
)
""".strip()

EDGE_BILLED_TO = """
CREATE UNDIRECTED EDGE BILLED_TO (
    FROM Txn, TO Address
)
""".strip()

EDGE_PURCHASER_EMAIL = """
CREATE UNDIRECTED EDGE PURCHASER_EMAIL (
    FROM Txn, TO EmailDomain
)
""".strip()

EDGE_RECIPIENT_EMAIL = """
CREATE UNDIRECTED EDGE RECIPIENT_EMAIL (
    FROM Txn, TO EmailDomain
)
""".strip()

EDGE_USED_DEVICE = """
CREATE UNDIRECTED EDGE USED_DEVICE (
    FROM Txn, TO Device
)
""".strip()

EDGES = [
    ("MADE_WITH_CARD", EDGE_MADE_WITH_CARD),
    ("BILLED_TO", EDGE_BILLED_TO),
    ("PURCHASER_EMAIL", EDGE_PURCHASER_EMAIL),
    ("RECIPIENT_EMAIL", EDGE_RECIPIENT_EMAIL),
    ("USED_DEVICE", EDGE_USED_DEVICE),
]

# ----------------------------------------------------------------- graph

VERTEX_NAMES = [name for name, _ in VERTICES]
EDGE_NAMES = [name for name, _ in EDGES]


def create_graph_statement(graph_name: str = GRAPH_NAME) -> str:
    types = ", ".join(VERTEX_NAMES + EDGE_NAMES)
    return f"CREATE GRAPH {graph_name} ({types})"


def all_statements(graph_name: str = GRAPH_NAME) -> list[tuple[str, str]]:
    """Every statement needed to deploy the schema, in dependency order."""
    stmts: list[tuple[str, str]] = list(VERTICES)
    stmts += EDGES
    stmts.append((f"CREATE GRAPH {graph_name}", create_graph_statement(graph_name)))
    return stmts
