"""GSQL schema for the official HHGOA_IEEE graph.

A separate graph from the development `HHGOA_FRAUD` graph (which holds the
Kaggle IEEE-CIS subset and is left untouched). The vertex/edge set follows
the official README's "Suggested graph schema", with two deliberate
differences:

  * `InvestigationCase` (+ its CASE_* edges) is added: the README requires
    each answer's case to be written into the graph as case memory.
  * The suggested `Transaction -NEXT-> Transaction` chain is omitted;
    card history is ordered by `ts` at query time instead.

Type names are prefixed `H` so they cannot collide with the development
graph's global vertex types (`Card`, `EmailDomain`, ...) on the same
TigerGraph instance.
"""

from __future__ import annotations

GRAPH_NAME = "HHGOA_IEEE"

VERTICES: list[tuple[str, str]] = [
    (
        "HCustomer",
        'CREATE VERTEX HCustomer (PRIMARY_ID customer_id STRING) WITH primary_id_as_attribute="true"',
    ),
    (
        "HCard",
        """CREATE VERTEX HCard (
        PRIMARY_ID card_id STRING, customer_id STRING,
        network STRING DEFAULT "", card_type STRING DEFAULT ""
    ) WITH primary_id_as_attribute="true\"""",
    ),
    (
        "HTxn",
        """CREATE VERTEX HTxn (
        PRIMARY_ID txn_id STRING,
        ts DATETIME, amount DOUBLE, product_cd STRING, channel STRING,
        risk_score DOUBLE, card_id STRING, customer_id STRING,
        addr1 STRING DEFAULT "", addr2 STRING DEFAULT "",
        dist1 DOUBLE DEFAULT -1,
        p_email STRING DEFAULT "", r_email STRING DEFAULT "",
        m1 STRING DEFAULT "", m2 STRING DEFAULT "", m3 STRING DEFAULT "",
        m4 STRING DEFAULT "", m5 STRING DEFAULT "", m6 STRING DEFAULT "",
        m7 STRING DEFAULT "", m8 STRING DEFAULT "", m9 STRING DEFAULT "",
        device_status STRING DEFAULT "", proxy_type STRING DEFAULT "",
        device_type STRING DEFAULT "", device_profile STRING DEFAULT ""
    ) WITH primary_id_as_attribute="true\"""",
    ),
    (
        "HDeviceProfile",
        'CREATE VERTEX HDeviceProfile (PRIMARY_ID profile STRING) WITH primary_id_as_attribute="true"',
    ),
    (
        "HEmailDomain",
        'CREATE VERTEX HEmailDomain (PRIMARY_ID domain STRING) WITH primary_id_as_attribute="true"',
    ),
    (
        "HBillingRegion",
        'CREATE VERTEX HBillingRegion (PRIMARY_ID addr1 STRING) WITH primary_id_as_attribute="true"',
    ),
    (
        "HClosedCase",
        """CREATE VERTEX HClosedCase (
        PRIMARY_ID case_id STRING, customer_id STRING, card_id STRING,
        opened_at DATETIME, closed_at DATETIME, outcome STRING, pattern STRING,
        n_txns INT, exposure_usd DOUBLE, actions_taken STRING,
        report_filed STRING, analyst_notes STRING
    ) WITH primary_id_as_attribute="true\"""",
    ),
    (
        "InvestigationCase",
        """CREATE VERTEX InvestigationCase (
        PRIMARY_ID graph_case_id STRING, official_case_id STRING,
        status STRING, verdict STRING, fraud_probability DOUBLE,
        pattern STRING, pattern_description STRING, exposure_usd DOUBLE,
        summary STRING, sar_file BOOL, initial_actions STRING,
        final_actions STRING, stop_reason STRING, written_at DATETIME
    ) WITH primary_id_as_attribute="true\"""",
    ),
]

EDGES: list[tuple[str, str]] = [
    ("H_OWNS", "CREATE UNDIRECTED EDGE H_OWNS (FROM HCustomer, TO HCard)"),
    ("H_MADE", "CREATE UNDIRECTED EDGE H_MADE (FROM HCard, TO HTxn)"),
    ("H_FROM_DEVICE", "CREATE UNDIRECTED EDGE H_FROM_DEVICE (FROM HTxn, TO HDeviceProfile)"),
    ("H_PURCHASER_EMAIL", "CREATE UNDIRECTED EDGE H_PURCHASER_EMAIL (FROM HTxn, TO HEmailDomain)"),
    ("H_RECIPIENT_EMAIL", "CREATE UNDIRECTED EDGE H_RECIPIENT_EMAIL (FROM HTxn, TO HEmailDomain)"),
    ("H_BILLED_IN", "CREATE UNDIRECTED EDGE H_BILLED_IN (FROM HTxn, TO HBillingRegion)"),
    ("H_INVOLVES", "CREATE UNDIRECTED EDGE H_INVOLVES (FROM HClosedCase, TO HTxn)"),
    ("H_ON_CARD", "CREATE UNDIRECTED EDGE H_ON_CARD (FROM HClosedCase, TO HCard)"),
    ("H_CONNECTED_TO", "CREATE UNDIRECTED EDGE H_CONNECTED_TO (FROM HClosedCase, TO HCard)"),
    ("CASE_FLAGGED", "CREATE UNDIRECTED EDGE CASE_FLAGGED (FROM InvestigationCase, TO HTxn)"),
    ("CASE_AFFECTS", "CREATE UNDIRECTED EDGE CASE_AFFECTS (FROM InvestigationCase, TO HTxn)"),
    ("CASE_ON_CARD", "CREATE UNDIRECTED EDGE CASE_ON_CARD (FROM InvestigationCase, TO HCard)"),
    (
        "CASE_CONNECTED_CARD",
        "CREATE UNDIRECTED EDGE CASE_CONNECTED_CARD (FROM InvestigationCase, TO HCard)",
    ),
    (
        "CASE_DEVICE",
        "CREATE UNDIRECTED EDGE CASE_DEVICE (FROM InvestigationCase, TO HDeviceProfile)",
    ),
    (
        "CASE_SIMILAR_TO",
        "CREATE UNDIRECTED EDGE CASE_SIMILAR_TO (FROM InvestigationCase, TO HClosedCase)",
    ),
]

VERTEX_NAMES = [n for n, _ in VERTICES]
EDGE_NAMES = [n for n, _ in EDGES]


def create_graph_statement(graph_name: str = GRAPH_NAME) -> str:
    return f"CREATE GRAPH {graph_name} ({', '.join(VERTEX_NAMES + EDGE_NAMES)})"


def all_statements(graph_name: str = GRAPH_NAME) -> list[tuple[str, str]]:
    return [*VERTICES, *EDGES, (f"CREATE GRAPH {graph_name}", create_graph_statement(graph_name))]
