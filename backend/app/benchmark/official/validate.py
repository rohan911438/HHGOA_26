"""Deterministic conformance checks for official answer files.

The HHGOA_IEEE package contains NO answer key ("We score them against an
answer key you don't have" - README). So nothing here measures accuracy.
What it does check, per case, is that the answer is well-formed against
the README's Answer Format and internally consistent with the Fraud
Policy - things that are objectively checkable without the key.
"""

from __future__ import annotations

import re

from app.benchmark.official import policy as P

STATUS = {"open", "closed_fraud", "closed_legitimate", "escalated"}
VERDICT = {"fraud", "legitimate", "uncertain"}
PATTERN = {
    "card_testing",
    "card_not_present_fraud",
    "card_not_present_new_device",
    "out_of_region_use",
    "account_takeover",
    "undocumented",
    "none",
}
EVIDENCE_SOURCE = {"graph", "document", "customer", "external"}
REQUEST_TYPE = {"customer_validation", "step_up_auth", "analyst_info"}
ROUTE = {"auto", "L1", "L2"}

TOP_FIELDS = (
    "case_id",
    "case",
    "evidence_requests",
    "next_best_actions",
    "sar",
    "stop_reason",
    "tool_calls",
    "tokens",
    "latency_s",
)
CASE_FIELDS = (
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
    "evidence",
    "similar_prior_cases",
    "summary",
    "written_to_graph",
    "graph_case_id",
)
SAR_FIELDS = ("file", "reason", "narrative", "subjects", "total_amount_usd", "activity_dates")


def check_answer(
    ans: dict,
    *,
    txn_amounts: dict[str, float],
    card_ids: set[str],
    customer_ids: set[str],
    closed_case_ids: set[str],
    device_profiles: set[str],
    official_case_ids: frozenset[str] | set[str] = frozenset(),
) -> list[dict]:
    out: list[dict] = []

    def c(name: str, ok: bool, detail: str = "") -> None:
        out.append({"check": name, "ok": bool(ok), "detail": detail})

    c(
        "top_level_fields",
        all(k in ans for k in TOP_FIELDS),
        str([k for k in TOP_FIELDS if k not in ans]),
    )
    case, sar, nba = ans.get("case", {}), ans.get("sar", {}), ans.get("next_best_actions", {})
    c(
        "case_fields",
        all(k in case for k in CASE_FIELDS),
        str([k for k in CASE_FIELDS if k not in case]),
    )
    c("sar_fields", all(k in sar for k in SAR_FIELDS))
    c("nba_fields", all(k in nba for k in ("initial", "final", "what_changed")))

    c("enum_status", case.get("status") in STATUS, case.get("status"))
    c("enum_verdict", case.get("verdict") in VERDICT, case.get("verdict"))
    c("enum_pattern", case.get("pattern") in PATTERN, case.get("pattern"))
    p = case.get("fraud_probability")
    c("probability_range", isinstance(p, (int, float)) and 0 <= p <= 1, str(p))
    c(
        "undocumented_has_description",
        case.get("pattern") != "undocumented" or len(case.get("pattern_description", "")) > 40,
    )
    c(
        "description_empty_unless_undocumented",
        case.get("pattern") == "undocumented" or case.get("pattern_description") == "",
    )

    affected = case.get("affected_txn_ids", [])
    c(
        "affected_ids_exist",
        all(t in txn_amounts for t in affected),
        str([t for t in affected if t not in txn_amounts]),
    )
    exp = round(sum(abs(txn_amounts.get(t, 0.0)) for t in affected), 2)
    c(
        "exposure_equals_sum_of_affected",
        abs(exp - float(case.get("exposure_usd", -1))) < 0.011,
        f"sum={exp} stated={case.get('exposure_usd')}",
    )
    fs = case.get("first_suspicious_txn_id", "")
    c("first_suspicious_in_affected", fs == "" if not affected else fs in affected)
    c("connected_cards_exist", all(x in card_ids for x in case.get("connected_card_ids", [])))
    c(
        "device_profiles_exist",
        all(x in device_profiles for x in case.get("connected_device_profiles", [])),
    )
    c(
        "similar_cases_exist",
        all(x in closed_case_ids for x in case.get("similar_prior_cases", [])),
        str([x for x in case.get("similar_prior_cases", []) if x not in closed_case_ids]),
    )
    ev = case.get("evidence", [])
    c(
        "evidence_well_formed",
        bool(ev)
        and all(
            set(e) >= {"claim", "source", "ref", "entity_ids"} and e["source"] in EVIDENCE_SOURCE
            for e in ev
        ),
    )
    known = set(txn_amounts) | card_ids | customer_ids | closed_case_ids | set(official_case_ids)
    ev_ids = [i for e in ev for i in e.get("entity_ids", [])]
    c(
        "evidence_ids_exist",
        all(i in known for i in ev_ids),
        str([i for i in ev_ids if i not in known][:5]),
    )

    if case.get("verdict") == "legitimate":
        c(
            "legit_has_no_episode",
            not affected and float(case.get("exposure_usd", 0)) == 0 and not sar.get("file"),
        )

    for phase in ("initial", "final"):
        recs = nba.get(phase, [])
        c(f"{phase}_nonempty", bool(recs))
        c(f"{phase}_actions_valid", all(r.get("action") in P.ACTIONS for r in recs))
        exposure = float(case.get("exposure_usd", 0))
        bad = (
            [r["action"] for r in recs if r.get("route") != P.route(r.get("action"), exposure)]
            if all(r.get("action") in P.ACTIONS for r in recs)
            else ["?"]
        )
        c(f"{phase}_routes_match_policy_s2", not bad, str(bad))
        c(
            f"{phase}_reasons_cite_rule",
            all(re.search(r"R\d+|§\d", r.get("reason", "")) for r in recs),
        )
    final_actions = [r.get("action") for r in nba.get("final", [])]
    reqs = ans.get("evidence_requests", [])
    c(
        "evidence_requests_well_formed",
        all(
            r.get("type") in REQUEST_TYPE
            and isinstance(r.get("asked_after_step"), int)
            and r.get("assumed_response")
            for r in reqs
        ),
    )
    c(
        "no_requests_means_final_equals_initial",
        bool(reqs) or nba.get("final") == nba.get("initial"),
    )
    c(
        "what_changed_consistent",
        (nba.get("what_changed") == "nothing") == (nba.get("final") == nba.get("initial"))
        or bool(reqs),
    )
    c(
        "case_opened_when_evidence_requested_s3a",
        not reqs
        or any(
            r.get("action") == "CREATE_CASE" for r in nba.get("initial", []) + nba.get("final", [])
        ),
    )
    c("block_all_cards_r10", "BLOCK_ALL_CARDS" not in final_actions)

    c("sar_file_matches_FILE_REPORT", bool(sar.get("file")) == ("FILE_REPORT" in final_actions))
    if sar.get("file"):
        n_sent = len(re.findall(r"[.!?](\s|$)", sar.get("narrative", "")))
        c("sar_narrative_6_to_12_sentences", 6 <= n_sent <= 12, str(n_sent))
        c(
            "sar_subjects_exist",
            all(s in card_ids | customer_ids | device_profiles for s in sar.get("subjects", [])),
        )
        c(
            "sar_total_equals_exposure",
            abs(float(sar.get("total_amount_usd", -1)) - float(case.get("exposure_usd", 0)))
            < 0.011,
        )
        c(
            "sar_dates_format",
            len(sar.get("activity_dates", [])) == 2
            and all(re.match(r"^\d{4}-\d{2}-\d{2}$", d) for d in sar["activity_dates"]),
        )
        c("sar_requires_fraud_verdict", case.get("verdict") == "fraud")
    else:
        c(
            "sar_empty_when_not_filed",
            sar.get("narrative") == ""
            and sar.get("subjects") == []
            and sar.get("total_amount_usd") == 0
            and sar.get("activity_dates") == [],
        )
    c("written_to_graph", case.get("written_to_graph") is True and bool(case.get("graph_case_id")))
    c(
        "counters_present",
        isinstance(ans.get("tool_calls"), int)
        and isinstance(ans.get("tokens"), int)
        and isinstance(ans.get("latency_s"), (int, float)),
    )
    return out
