"""Case summary and SAR block for the official answer file.

Deterministic text assembled only from graph-retrieved facts and the
decision already made, so every ID and amount in it exists in the dataset
(README Rules: "Every ID in your answer files must exist"). The SAR
narrative follows the README's who/what/when/where/how/why structure
(FinCEN SAR Narrative Guidance) in six to twelve sentences.
"""

from __future__ import annotations

from app.benchmark.official import policy as P
from app.benchmark.official.signals import SignalSet, parse_ts

PATTERN_WORDS = {
    "card_testing": "card testing (small authorizations probing a stolen number, then a larger purchase)",
    "card_not_present_fraud": "card-not-present fraud",
    "card_not_present_new_device": "card-not-present fraud from a device new to the account",
    "out_of_region_use": "card-present use in a billing region the cardholder has no history in",
    "account_takeover": "account takeover (credential compromise with device and match-flag anomalies)",
    "undocumented": "a pattern outside the bank's five documented typologies",
    "none": "no fraud pattern",
}


def summary(case, case_obj: dict, decision: dict, sig: SignalSet) -> str:
    verdict = case_obj["verdict"]
    first = f"{case.trigger_type.replace('_', ' ').capitalize()} alert on {case.card_id} (transaction {case.flagged_txn_id})."
    if verdict == "legitimate":
        why = [
            s.detail
            for n, s in sig.signals.items()
            if s.present and n in ("known_device", "multi_day_stay", "recurring_charge")
        ]
        body = (
            f" Assessed as legitimate, final fraud probability {case_obj['fraud_probability']}."
            + (f" Supporting: {why[0]}." if why else "")
            + (
                " " + decision["evidence_requests"][0]["assumed_response"]
                if decision["evidence_requests"]
                else ""
            )
        )
        return first + body + f" Actions: {', '.join(r['action'] for r in decision['final'])}."
    n = len(case_obj["affected_txn_ids"])
    if verdict == "uncertain":
        return (
            first
            + f" Verdict uncertain (probability {case_obj['fraud_probability']}): the cardholder denies the "
            f"${case_obj['exposure_usd']:,.2f} purchase, but the graph shows no anomaly on the card, device or region "
            "to confirm or refute it. The card is blocked on the denial (R2) and the case escalated to an analyst (R8)."
            + f" Final actions: {', '.join(r['action'] for r in decision['final'])}."
        )
    body = (
        f" Assessed as {PATTERN_WORDS.get(case_obj['pattern'], case_obj['pattern'])}, fraud probability "
        f"{case_obj['fraud_probability']}: {n} transaction(s) totalling ${case_obj['exposure_usd']:,.2f} starting at "
        f"{case_obj['first_suspicious_txn_id']}."
    )
    if case_obj["connected_card_ids"]:
        body += f" The device profile links {len(case_obj['connected_card_ids'])} other card(s)."
    return first + body + f" Final actions: {', '.join(r['action'] for r in decision['final'])}."


def sar_block(case, case_obj: dict, decision: dict, by_id: dict, sig: SignalSet) -> dict:
    final_actions = {r["action"] for r in decision["final"]}
    file = "FILE_REPORT" in final_actions
    _, reason = P.sar_required(
        case_obj["verdict"],
        case_obj["fraud_probability"],
        case_obj["exposure_usd"],
        shared_origin=sig.on("shared_device_ring"),
        undocumented_or_coordinated=case_obj["pattern"] == "undocumented",
    )
    if not file:
        return {
            "file": False,
            "reason": reason,
            "narrative": "",
            "subjects": [],
            "total_amount_usd": 0,
            "activity_dates": [],
        }

    txns = sorted(
        (by_id[i] for i in case_obj["affected_txn_ids"] if i in by_id),
        key=lambda t: parse_ts(t["ts"]),
    )
    d0, d1 = parse_ts(txns[0]["ts"]), parse_ts(txns[-1]["ts"])
    channels = sorted({t["channel"] for t in txns})
    regions = sorted({t["addr1"] for t in txns if t.get("addr1")})
    products = sorted({t["product_cd"] for t in txns})
    devices = sorted({t["device_profile"] for t in txns if t.get("device_profile")})
    amounts = ", ".join(f"${float(t['amount']):,.2f}" for t in txns[:8]) + (
        " and others" if len(txns) > 8 else ""
    )
    subjects = [case.customer_id, case.card_id, *case_obj["connected_card_ids"]]

    s = []
    when = (
        f"On {d0:%Y-%m-%d} at {d0:%H:%M}"
        if d0 == d1
        else f"Between {d0:%Y-%m-%d %H:%M} and {d1:%Y-%m-%d %H:%M}"
    )
    s.append(
        f"{when}, card {case.card_id} held by customer {case.customer_id} was used for "
        + (
            f"one transaction of {amounts}."
            if len(txns) == 1
            else f"{len(txns)} transactions of {amounts}, totalling ${case_obj['exposure_usd']:,.2f}."
        )
    )
    s.append(
        f"The activity was {', '.join(channels)}"
        + (f", billed in region(s) {', '.join(regions)}" if regions else "")
        + f", under product code(s) {', '.join(products)}."
    )
    if devices:
        s.append(f"The online transactions came from device profile(s) {'; '.join(devices[:2])}.")
    s.append(
        f"The alert was raised by {case.trigger_type.replace('_', ' ')} on transaction {case.flagged_txn_id}"
        + (f" (model score {case.risk_score})." if case.risk_score is not None else ".")
    )
    s.append(
        f"The investigation found {PATTERN_WORDS.get(case_obj['pattern'], case_obj['pattern'])}."
    )
    for name in (
        "card_testing_sequence",
        "sub_threshold_structuring",
        "online_burst_48h",
        "new_device",
        "proxy",
        "new_billing_region",
        "home_activity_continues",
        "match_flag_anomaly",
    ):
        # at most three "how" sentences, so who/when/why always fit in twelve
        if sig.on(name) and sum(1 for x in s if x.startswith("§how")) < 3:
            s.append(
                "§how" + sig.signals[name].detail[0].upper() + sig.signals[name].detail[1:] + "."
            )
    if sig.on("shared_device_ring"):
        s.append(
            f"The same device profile was used on {len(case_obj['connected_card_ids'])} other customers' cards "
            f"within 30 days, including {', '.join(case_obj['connected_card_ids'][:5])}, indicating a common actor."
        )
    if decision["evidence_requests"]:
        s.append("Cardholder contact: " + decision["evidence_requests"][0]["assumed_response"])
    elif case.trigger_type == "customer_report":
        s.append("The cardholder reported that they did not make the transaction.")
    s.append(f"It is suspicious because {reason[0].lower() + reason[1:]}.")
    s.append("Actions: " + ", ".join(r["action"] for r in decision["final"]) + ".")
    narrative = " ".join(x.removeprefix("§how") for x in s)
    return {
        "file": True,
        "reason": reason,
        "narrative": narrative,
        "subjects": subjects,
        "total_amount_usd": case_obj["exposure_usd"],
        "activity_dates": [f"{d0:%Y-%m-%d}", f"{d1:%Y-%m-%d}"],
    }
