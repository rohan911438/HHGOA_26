"""Read/write tools over the official HHGOA_IEEE TigerGraph graph.

Each tool is one GSQL interpreted query (or one REST upsert for the case
write). The agent only reaches the graph through these, and every call is
counted, so `tool_calls` in the answer file is a real number.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import requests

from app.benchmark.official.graph_schema import GRAPH_NAME
from app.tigergraph.client import TigerGraphClient

_TRANSIENT = (requests.exceptions.ConnectionError, ConnectionError, requests.exceptions.ReadTimeout)

Q_TXN = f"""
INTERPRET QUERY (VERTEX<HTxn> t) FOR GRAPH {GRAPH_NAME} {{
  S = {{t}};
  PRINT S;
}}"""

Q_CARD_HISTORY = f"""
INTERPRET QUERY (VERTEX<HCard> c) FOR GRAPH {GRAPH_NAME} {{
  S = {{c}};
  T = SELECT t FROM S:s -(H_MADE)- HTxn:t;
  PRINT T;
}}"""

Q_CUSTOMER_CARDS = f"""
INTERPRET QUERY (VERTEX<HCustomer> cu) FOR GRAPH {GRAPH_NAME} {{
  S = {{cu}};
  C = SELECT c FROM S:s -(H_OWNS)- HCard:c;
  PRINT C;
}}"""

Q_DEVICE_NEIGHBOURS = f"""
INTERPRET QUERY (VERTEX<HDeviceProfile> d, DATETIME t0, DATETIME t1) FOR GRAPH {GRAPH_NAME} {{
  SumAccum<INT> @n;
  SumAccum<DOUBLE> @amt;
  MinAccum<DATETIME> @first;
  MaxAccum<DATETIME> @last;
  SumAccum<INT> @@new_all, @@anon_all;
  S = {{d}};
  T = SELECT t FROM S:s -(H_FROM_DEVICE)- HTxn:t WHERE t.ts >= t0 AND t.ts <= t1;
  C = SELECT c FROM T:t -(H_MADE)- HCard:c
      ACCUM c.@n += 1, c.@amt += t.amount, c.@first += t.ts, c.@last += t.ts;
  ALL_T = SELECT t FROM S:s -(H_FROM_DEVICE)- HTxn:t
      ACCUM IF t.device_status == "New" THEN @@new_all += 1 END,
            IF t.proxy_type == "IP_PROXY:ANONYMOUS" OR t.proxy_type == "IP_PROXY:HIDDEN" THEN @@anon_all += 1 END;
  ALL_C = SELECT c FROM ALL_T:t -(H_MADE)- HCard:c;
  K = SELECT k FROM ALL_T:t -(H_INVOLVES)- HClosedCase:k WHERE k.outcome == "confirmed_fraud";
  PRINT T.size() AS window_txns, ALL_T.size() AS all_txns, ALL_C.size() AS all_cards,
        @@new_all AS new_all, @@anon_all AS anon_proxy_all;
  PRINT C[C.card_id, C.customer_id, C.@n, C.@amt, C.@first, C.@last];
  PRINT K[K.case_id, K.card_id, K.pattern, K.opened_at];
}}"""

Q_CLOSED_FOR_CARDS = f"""
INTERPRET QUERY (SET<VERTEX<HCard>> cards) FOR GRAPH {GRAPH_NAME} {{
  S = cards;
  K = SELECT k FROM S:s -((H_ON_CARD|H_CONNECTED_TO))- HClosedCase:k;
  PRINT K;
}}"""

Q_SIMILAR_CLOSED = f"""
INTERPRET QUERY (STRING pat, DOUBLE lo, DOUBLE hi, DATETIME before_ts) FOR GRAPH {GRAPH_NAME} {{
  K = SELECT k FROM HClosedCase:k
      WHERE k.pattern == pat AND k.exposure_usd >= lo AND k.exposure_usd <= hi AND k.closed_at < before_ts
      LIMIT 40;
  PRINT K;
}}"""

Q_CASE_MEMORY = f"""
INTERPRET QUERY (SET<VERTEX<HCard>> cards, SET<VERTEX<HDeviceProfile>> devices) FOR GRAPH {GRAPH_NAME} {{
  S = cards;
  D = devices;
  A = SELECT ic FROM S:s -((CASE_ON_CARD|CASE_CONNECTED_CARD))- InvestigationCase:ic;
  B = SELECT ic FROM D:d -(CASE_DEVICE)- InvestigationCase:ic;
  R = A UNION B;
  PRINT R;
}}"""

Q_CASE_MEMORY_CARDS = f"""
INTERPRET QUERY (SET<VERTEX<HCard>> cards) FOR GRAPH {GRAPH_NAME} {{
  S = cards;
  R = SELECT ic FROM S:s -((CASE_ON_CARD|CASE_CONNECTED_CARD))- InvestigationCase:ic;
  PRINT R;
}}"""

Q_CASE_READBACK = f"""
INTERPRET QUERY (VERTEX<InvestigationCase> ic) FOR GRAPH {GRAPH_NAME} {{
  S = {{ic}};
  F = SELECT t FROM S:s -(CASE_FLAGGED)- HTxn:t;
  A = SELECT t FROM S:s -(CASE_AFFECTS)- HTxn:t;
  C = SELECT c FROM S:s -((CASE_ON_CARD|CASE_CONNECTED_CARD))- HCard:c;
  D = SELECT d FROM S:s -(CASE_DEVICE)- HDeviceProfile:d;
  K = SELECT k FROM S:s -(CASE_SIMILAR_TO)- HClosedCase:k;
  PRINT S;
  PRINT F.size() AS flagged, A.size() AS affected, C.size() AS cards, D.size() AS devices, K.size() AS similar;
}}"""


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _attrs(v: dict) -> dict:
    a = dict(v.get("attributes", {}))
    a.setdefault("txn_id", v.get("v_id"))
    return a


@dataclass
class ToolCall:
    tool: str
    params: dict
    latency_ms: float
    ok: bool
    error: str = ""


@dataclass
class OfficialGraphTools:
    client: TigerGraphClient
    calls: list[ToolCall] = field(default_factory=list)

    def _run(self, name: str, query: str, params: dict) -> list[Any]:
        conn = self.client.connection
        last_exc: Exception | None = None
        for attempt in range(3):
            started = time.perf_counter()
            try:
                res = conn.runInterpretedQuery(query, params=params)
                self.calls.append(
                    ToolCall(name, params, round((time.perf_counter() - started) * 1000, 1), True)
                )
                return res
            except _TRANSIENT as exc:
                last_exc = exc
                time.sleep(1.5 * (attempt + 1))
            except Exception as exc:
                self.calls.append(
                    ToolCall(
                        name,
                        params,
                        round((time.perf_counter() - started) * 1000, 1),
                        False,
                        f"{type(exc).__name__}: {exc}",
                    )
                )
                raise
        self.calls.append(ToolCall(name, params, 0.0, False, f"transient: {last_exc}"))
        raise RuntimeError(f"{name} failed after retries: {last_exc}")

    # ---------------------------------------------------------------- reads

    def get_transaction(self, txn_id: str) -> dict:
        res = self._run("get_transaction", Q_TXN, {"t": txn_id})
        rows = res[0]["S"]
        if not rows:
            raise LookupError(f"transaction {txn_id} not in graph")
        return _attrs(rows[0])

    def card_history(self, card_id: str) -> list[dict]:
        res = self._run("card_history", Q_CARD_HISTORY, {"c": card_id})
        return [_attrs(v) for v in res[0]["T"]]

    def customer_cards(self, customer_id: str) -> list[str]:
        res = self._run("customer_cards", Q_CUSTOMER_CARDS, {"cu": customer_id})
        return sorted(v["v_id"] for v in res[0]["C"])

    def device_neighbours(self, profile: str, around: datetime, days: int = 30) -> dict:
        res = self._run(
            "device_neighbours",
            Q_DEVICE_NEIGHBOURS,
            {
                "d": profile,
                "t0": _fmt(around - timedelta(days=days)),
                "t1": _fmt(around + timedelta(days=days)),
            },
        )
        counts = res[0]
        cards = [
            {
                "card_id": v["attributes"]["C.card_id"],
                "customer_id": v["attributes"]["C.customer_id"],
                "n": v["attributes"]["C.@n"],
                "amount": round(v["attributes"]["C.@amt"], 2),
                "first": v["attributes"]["C.@first"],
                "last": v["attributes"]["C.@last"],
            }
            for v in res[1]["C"]
        ]
        cases = sorted({v["attributes"]["K.case_id"] for v in res[2]["K"]})
        return {
            "profile": profile,
            "window_txns": counts["window_txns"],
            "all_txns": counts["all_txns"],
            "all_cards": counts["all_cards"],
            "new_share_all": round(counts["new_all"] / counts["all_txns"], 3)
            if counts["all_txns"]
            else 0.0,
            "anon_proxy_share_all": round(counts["anon_proxy_all"] / counts["all_txns"], 3)
            if counts["all_txns"]
            else 0.0,
            "cards": cards,
            "confirmed_fraud_cases": cases,
            "case_cards": sorted({v["attributes"]["K.card_id"] for v in res[2]["K"]}),
        }

    def closed_cases_for_cards(self, card_ids: list[str]) -> list[dict]:
        if not card_ids:
            return []
        res = self._run("closed_cases_for_cards", Q_CLOSED_FOR_CARDS, {"cards": card_ids})
        return [dict(v["attributes"], case_id=v["v_id"]) for v in res[0]["K"]]

    def similar_closed_cases(self, pattern: str, exposure: float, before: datetime) -> list[dict]:
        lo, hi = max(0.0, exposure * 0.5), max(exposure * 1.5, 50.0)
        res = self._run(
            "similar_closed_cases",
            Q_SIMILAR_CLOSED,
            {"pat": pattern, "lo": lo, "hi": hi, "before_ts": _fmt(before)},
        )
        return [dict(v["attributes"], case_id=v["v_id"]) for v in res[0]["K"]]

    def case_memory(self, card_ids: list[str], profiles: list[str]) -> list[dict]:
        # pyTigerGraph omits empty SET parameters (measured: "Missing parameter:
        # devices" on an in-person alert), so the device clause is dropped then.
        if profiles:
            res = self._run("case_memory", Q_CASE_MEMORY, {"cards": card_ids, "devices": profiles})
        else:
            res = self._run("case_memory", Q_CASE_MEMORY_CARDS, {"cards": card_ids})
        return [dict(v["attributes"], graph_case_id=v["v_id"]) for v in res[0]["R"]]

    # ---------------------------------------------------------------- write

    def write_case(
        self,
        graph_case_id: str,
        attrs: dict,
        *,
        flagged: str,
        affected: list[str],
        card: str,
        connected_cards: list[str],
        devices: list[str],
        similar: list[str],
    ) -> dict:
        conn = self.client.connection
        started = time.perf_counter()
        n_v = conn.upsertVertex("InvestigationCase", graph_case_id, attrs)
        edges = [
            ("CASE_FLAGGED", "HTxn", [flagged]),
            ("CASE_AFFECTS", "HTxn", affected),
            ("CASE_ON_CARD", "HCard", [card]),
            ("CASE_CONNECTED_CARD", "HCard", connected_cards),
            ("CASE_DEVICE", "HDeviceProfile", devices),
            ("CASE_SIMILAR_TO", "HClosedCase", similar),
        ]
        n_e = 0
        for etype, ttype, targets in edges:
            if targets:
                n_e += conn.upsertEdges(
                    "InvestigationCase", etype, ttype, [(graph_case_id, t, {}) for t in targets]
                )
        self.calls.append(
            ToolCall(
                "write_case",
                {"graph_case_id": graph_case_id},
                round((time.perf_counter() - started) * 1000, 1),
                True,
            )
        )
        return {
            "vertices_upserted": n_v,
            "edges_upserted": n_e,
            "edges_expected": sum(len(t) for _, _, t in edges),
        }

    def read_case(self, graph_case_id: str) -> dict:
        res = self._run("read_case", Q_CASE_READBACK, {"ic": graph_case_id})
        rows = res[0]["S"]
        return {
            "vertex": rows[0]["attributes"] if rows else None,
            **(res[1] if len(res) > 1 else {}),
        }
