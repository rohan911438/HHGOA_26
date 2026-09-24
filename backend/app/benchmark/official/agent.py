"""Official-benchmark investigation agent.

Runs the challenge's core flow for one case from `case_pack.csv`:

  1 trigger -> 2 open case, pull flagged txn -> 3 card + customer history
  -> 4 device neighbourhood -> 5 closed-case memory + our own prior cases
  -> 6 pattern signals + probability (uncertainty) -> 7 initial next best
  action -> 8 evidence request (simulated reply) -> 9 re-assess -> 10 final
  next best action -> 11 SAR decision -> 12 write case to graph, read back.

Everything the answer file says is traceable to a tool call (graph) or to
the policy text (document) or to a stated, simulated customer reply.

Simulated replies: the README says replies are not provided and must be
simulated and stated. This agent's single, uniform rule: the assumed reply
is the one its own evidence-based probability favours (>= 0.5: the customer
denies / step-up fails; < 0.5: the customer confirms). It is recorded in
`evidence_requests[].assumed_response` for every case.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.benchmark.official import policy as P
from app.benchmark.official.dataset import OfficialCase
from app.benchmark.official.signals import SignalSet, compute_signals, parse_ts
from app.benchmark.official.tools import OfficialGraphTools

# ------------------------------------------------------------------ scoring
#
# Log-odds contributions. Chosen by hand from the README's own wording of
# signal strength (e.g. pattern 3 "stronger than pattern 2, still not
# proof"; risk score "often wrong in both directions"), not fitted to any
# answer - there is no answer key in the package.

PRIOR = {"risk_score": 0.30, "customer_report": 0.50, "analyst_request": 0.40}

WEIGHTS = {
    "card_testing_sequence": 3.0,
    "sub_threshold_structuring": 2.5,
    "shared_device_ring": 2.0,
    "device_in_confirmed_fraud": 1.0,
    "online_burst_48h": 1.0,
    "online_amount_or_product_unusual": 0.8,
    "new_device": 0.8,
    "proxy": 0.6,
    "no_prior_online_use": 0.4,
    "match_flag_anomaly": 0.8,
    "purchaser_email_change": 0.5,
    "mixed_channel_48h": 0.2,
    "new_billing_region": 0.8,
    "home_activity_continues": 1.0,
    "multi_day_stay": -1.5,
    "known_device": -1.2,
    "recurring_charge": -2.5,
}
# Signals that count as independent pieces of evidence for §6 stopping and
# R1 "single signal". Correlated sub-signals are grouped.
EVIDENCE_GROUPS = {
    "sequence": ("card_testing_sequence", "sub_threshold_structuring", "online_burst_48h"),
    "amount": ("online_amount_or_product_unusual", "no_prior_online_use"),
    "device": ("new_device", "proxy"),
    "network": ("shared_device_ring", "device_in_confirmed_fraud"),
    "credentials": ("match_flag_anomaly", "purchaser_email_change"),
    "region": ("new_billing_region", "home_activity_continues"),
}
LEGIT_GROUPS = {
    "trip": ("multi_day_stay",),
    "device": ("known_device",),
    "recurring": ("recurring_charge",),
}

DENIAL_LOGODDS = 1.5  # a customer denial (trigger or simulated reply)
CONFIRMATION_LOGODDS = -3.0


def _logit(p: float) -> float:
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


@dataclass
class Assessment:
    probability: float
    fraud_groups: list[str]
    legit_groups: list[str]
    contributions: dict[str, float]


def assess(case: OfficialCase, sig: SignalSet, extra_logodds: float = 0.0) -> Assessment:
    x = _logit(PRIOR[case.trigger_type])
    contrib: dict[str, float] = {}
    for name, w in WEIGHTS.items():
        if sig.on(name):
            x += w
            contrib[name] = w
    if case.risk_score is not None:
        rs = 0.5 * (case.risk_score - 0.5) * 2  # README: an input, often wrong - small weight
        x += rs
        contrib["risk_score"] = round(rs, 3)
    x += extra_logodds
    fraud_groups = [g for g, names in EVIDENCE_GROUPS.items() if any(sig.on(n) for n in names)]
    if case.trigger_type == "customer_report":
        fraud_groups.append("customer_denial")
    legit_groups = [g for g, names in LEGIT_GROUPS.items() if any(sig.on(n) for n in names)]
    return Assessment(round(_sigmoid(x), 2), fraud_groups, legit_groups, contrib)


def classify(case: OfficialCase, sig: SignalSet, flagged: dict) -> tuple[str, str]:
    online = flagged.get("channel") == "online"
    if sig.on("card_testing_sequence"):
        return "card_testing", ""
    if sig.on("sub_threshold_structuring"):
        s = sig.signals["sub_threshold_structuring"]
        return "undocumented", (
            f"Rapid online purchases each just under a $500 authorization level ({s.detail}), consistent with "
            "amounts chosen to stay below a review threshold. It fits none of the five documented patterns: there "
            "is no low-value testing run and no new region. Found by scanning the card's 48-hour window for "
            "clusters of near-threshold amounts."
        )
    if sig.on("shared_device_ring"):
        return "undocumented", (
            f"One device profile ({sig.shared_device_profile}) is transacting on cards of several unrelated "
            f"customers within a month ({len(sig.ring_cards)} other card(s)), a coordinated multi-victim scheme "
            "rather than a single stolen number. Found by traversing the graph from the flagged transaction to its "
            "device profile and back out to every card that used it."
        )
    if (
        online
        and sig.on("match_flag_anomaly")
        and (sig.on("new_device") or sig.on("purchaser_email_change"))
        and sig.on("mixed_channel_48h")
    ):
        return "account_takeover", ""
    if not online and sig.on("new_billing_region") and not sig.on("multi_day_stay"):
        return "out_of_region_use", ""
    if (
        online
        and sig.on("new_device")
        and (sig.on("online_amount_or_product_unusual") or sig.on("online_burst_48h"))
    ):
        return "card_not_present_new_device", ""
    if online and (sig.on("online_amount_or_product_unusual") or sig.on("online_burst_48h")):
        return "card_not_present_fraud", ""
    return "none", ""


@dataclass
class Step:
    n: int
    name: str
    detail: str


@dataclass
class CaseRun:
    case: OfficialCase
    steps: list[Step] = field(default_factory=list)
    answer: dict = field(default_factory=dict)
    trace: dict = field(default_factory=dict)

    def step(self, name: str, detail: str) -> int:
        self.steps.append(Step(len(self.steps) + 1, name, detail))
        return len(self.steps)


class OfficialInvestigator:
    def __init__(self, tools: OfficialGraphTools, *, write_to_graph: bool = True) -> None:
        self.tools = tools
        self.write_to_graph = write_to_graph

    def run(self, case: OfficialCase) -> CaseRun:
        started = time.perf_counter()
        calls_before = len(self.tools.calls)
        run = CaseRun(case)
        t = self.tools
        opened = parse_ts(case.opened_at)

        run.step("trigger", f"{case.trigger_type}: {case.trigger_text}")

        flagged = t.get_transaction(case.flagged_txn_id)
        run.step(
            "open_case",
            f"flagged txn {case.flagged_txn_id} ${float(flagged['amount']):.2f} "
            f"{flagged['channel']} at {flagged['ts']}",
        )

        history = t.card_history(case.card_id)
        cards = t.customer_cards(case.customer_id)
        run.step("history", f"{len(history)} txns on {case.card_id}; customer holds {cards}")

        device = None
        if flagged.get("device_profile"):
            device = t.device_neighbours(flagged["device_profile"], parse_ts(flagged["ts"]))
        run.step(
            "device_neighbourhood",
            f"{len(device['cards']) if device else 0} card(s) on the device profile within 30 days"
            if device
            else "no device record (in-person)",
        )

        sig = compute_signals(flagged, history, device)
        pattern, pattern_desc = classify(case, sig, flagged)

        own_closed = t.closed_cases_for_cards(cards)
        episode_amt = sum(
            abs(float(x["amount"])) for x in history if str(x["txn_id"]) in sig.episode_txn_ids
        )
        similar = t.similar_closed_cases(
            pattern, max(episode_amt, float(flagged["amount"])), opened
        )
        memory = t.case_memory(
            [case.card_id, *sig.ring_cards[:20]],
            [flagged["device_profile"]] if sig.linkable_device else [],
        )
        run.step(
            "memory",
            f"{len(own_closed)} closed case(s) on the customer's cards; {len(similar)} similar "
            f"'{pattern}' closed case(s); {len(memory)} earlier agent case(s) linked",
        )

        a0 = assess(case, sig)
        assess_step = run.step(
            "assess", f"p={a0.probability} fraud evidence {a0.fraud_groups} legit {a0.legit_groups}"
        )

        by_id = {str(x["txn_id"]): x for x in history}
        by_id[str(flagged["txn_id"])] = flagged

        decision = decide(case, sig, pattern, a0, flagged, by_id, assess_step)
        if (
            decision["verdict"] == "fraud"
            and pattern == "none"
            and flagged.get("channel") == "online"
        ):
            # Fraud confirmed by the cardholder's denial with no more specific
            # signal: README pattern 2 is "the number is used online without
            # the card"; pattern 3 adds an identity record marking the device New.
            pattern = (
                "card_not_present_new_device" if sig.on("new_device") else "card_not_present_fraud"
            )
        # §3b: re-assess after the (simulated) evidence
        for _ in decision["evidence_requests"]:
            run.step("evidence_request", decision["evidence_requests"][0]["assumed_response"])
        run.step("final_decision", ", ".join(r["action"] for r in decision["final"]))

        connected_cards = decision["connected_cards"]
        devices = [sig.shared_device_profile] if sig.shared_device_profile else []
        similar_ids = _pick_similar(own_closed, similar, device, decision["verdict"])

        graph_case_id = f"CASE-{case.case_id}"
        evidence = _evidence(
            case, flagged, sig, a0, decision, device, own_closed, similar_ids, memory
        )
        exposure = decision["exposure"]
        case_obj = {
            "status": decision["status"],
            "verdict": decision["verdict"],
            "fraud_probability": decision["final_probability"],
            "pattern": pattern if decision["verdict"] != "legitimate" else "none",
            "pattern_description": pattern_desc if decision["verdict"] != "legitimate" else "",
            "affected_txn_ids": decision["affected"],
            "first_suspicious_txn_id": decision["affected"][0] if decision["affected"] else "",
            "connected_card_ids": connected_cards,
            "connected_device_profiles": devices,
            "exposure_usd": exposure,
            "evidence": evidence,
            "similar_prior_cases": similar_ids,
            "summary": "",
            "written_to_graph": False,
            "graph_case_id": "",
        }
        from app.benchmark.official.narrative import sar_block, summary

        sar = sar_block(case, case_obj, decision, by_id, sig)
        case_obj["summary"] = summary(case, case_obj, decision, sig)

        graph_write = {"attempted": False}
        if self.write_to_graph:
            attrs = {
                "official_case_id": case.case_id,
                "status": case_obj["status"],
                "verdict": case_obj["verdict"],
                "fraud_probability": case_obj["fraud_probability"],
                "pattern": case_obj["pattern"],
                "pattern_description": case_obj["pattern_description"],
                "exposure_usd": exposure,
                "summary": case_obj["summary"],
                "sar_file": sar["file"],
                "initial_actions": "|".join(r["action"] for r in decision["initial"]),
                "final_actions": "|".join(r["action"] for r in decision["final"]),
                "stop_reason": decision["stop_reason"],
                "written_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            }
            w = t.write_case(
                graph_case_id,
                attrs,
                flagged=case.flagged_txn_id,
                affected=decision["affected"],
                card=case.card_id,
                connected_cards=connected_cards,
                devices=devices,
                similar=similar_ids,
            )
            back = t.read_case(graph_case_id)
            v = back.get("vertex") or {}
            verified = bool(
                v
                and v.get("official_case_id") == case.case_id
                and v.get("verdict") == case_obj["verdict"]
                and v.get("final_actions") == attrs["final_actions"]
                and back.get("flagged") == 1
                and back.get("affected") == len(decision["affected"])
                and back.get("similar") == len(similar_ids)
            )
            graph_write = {"attempted": True, "write": w, "read_back": back, "verified": verified}
            case_obj["written_to_graph"] = verified
            case_obj["graph_case_id"] = graph_case_id if verified else ""
            run.step("write_case", f"{graph_case_id} written and read back: verified={verified}")

        n_calls = len(self.tools.calls) - calls_before
        run.answer = {
            "case_id": case.case_id,
            "case": case_obj,
            "evidence_requests": decision["evidence_requests"],
            "next_best_actions": {
                "initial": decision["initial"],
                "final": decision["final"],
                "what_changed": decision["what_changed"],
            },
            "sar": sar,
            "stop_reason": decision["stop_reason"],
            "tool_calls": n_calls,
            "tokens": 0,
            "latency_s": round(time.perf_counter() - started, 1),
        }
        run.trace = {
            "flagged": flagged,
            "card_history_size": len(history),
            "customer_cards": cards,
            "device_neighbourhood": device,
            "signals": {k: vars(v) for k, v in sig.signals.items()},
            "episode_txn_ids": sig.episode_txn_ids,
            "initial_assessment": vars(a0),
            "decision": {k: v for k, v in decision.items() if k not in ("initial", "final")},
            "own_closed_cases": [c["case_id"] for c in own_closed],
            "similar_retrieved": [c["case_id"] for c in similar],
            "agent_case_memory": [m["graph_case_id"] for m in memory],
            "graph_write": graph_write,
            "steps": [vars(s) for s in run.steps],
            "tool_calls": [vars(c) for c in self.tools.calls[calls_before:]],
        }
        return run


def _pick_similar(
    own: list[dict], similar: list[dict], device: dict | None, verdict: str
) -> list[str]:
    ids: list[str] = []
    ids += [c["case_id"] for c in own][:3]
    if device:
        ids += device["confirmed_fraud_cases"][:3]
    ids += [c["case_id"] for c in similar][:3]
    out: list[str] = []
    for i in ids:
        if i not in out:
            out.append(i)
    return out[:6]


# ------------------------------------------------------------------ decide


def decide(
    case: OfficialCase,
    sig: SignalSet,
    pattern: str,
    a0: Assessment,
    flagged: dict,
    by_id: dict,
    assess_step: int,
) -> dict:
    """Initial NBA -> (simulated) evidence -> final NBA, per policy R1-R10."""
    customer_report = case.trigger_type == "customer_report"
    episode = [i for i in sig.episode_txn_ids if i in by_id]
    amounts = [abs(float(by_id[i]["amount"])) for i in episode]
    exposure = round(sum(amounts), 2)
    ring = sig.on("shared_device_ring")
    connected = sig.ring_cards if ring else []
    coordinated = pattern == "undocumented"
    p0 = a0.probability
    initial: list[P.Rec] = []
    requests: list[dict] = []
    p_final = p0

    # ---------------------------------------------------------- R7
    if customer_report and sig.on("recurring_charge"):
        initial = [
            P.Rec("CREATE_CASE", "R7, §3a: customer disputes a charge"),
            P.Rec(
                "VERIFY_WITH_CUSTOMER",
                "R7: disputed charge matches the card's own monthly recurring "
                "charge; verify rather than block",
            ),
            P.Rec("WARN_CUSTOMER", "R7: recurring-charge reminder"),
        ]
        requests.append(
            {
                "type": "customer_validation",
                "asked_after_step": assess_step,
                "assumed_response": "Customer recognises the charge as their monthly recurring payment "
                "once reminded (simulated: probability favoured legitimate).",
            }
        )
        p_final = round(_sigmoid(_logit(max(p0, 0.02)) + CONFIRMATION_LOGODDS), 2)
        final = [
            P.Rec("CREATE_CASE", "R7, §3a: record the dispute and its resolution"),
            P.Rec("WARN_CUSTOMER", "R7: recurring-charge reminder sent"),
            P.Rec("CLOSE_NO_FRAUD", "R3, R7: customer confirmed the recurring charge; no block"),
        ]
        return _pack(
            "legitimate",
            "closed_legitimate",
            p_final,
            [],
            0.0,
            [],
            initial,
            final,
            requests,
            "Customer confirmation of the recurring charge settled the dispute (§6); nothing further "
            "would change the decision.",
            "The simulated confirmation turned the verify request into a no-fraud closure; the warning "
            "stands.",
        )

    # ---------------------------------------------------------- customer denial already in hand (R2)
    if customer_report:
        p_final = p0  # prior for customer_report already carries the denial
        corroboration = [g for g in a0.fraud_groups if g != "customer_denial"]
        if not corroboration and not a0.legit_groups and flagged.get("channel") == "in_person":
            # The denial is the only evidence either way: card-present, no new
            # region, nothing on the graph for or against. R2 still applies to
            # a denial; R8 escalates because the evidence cannot settle it.
            recs = [
                P.Rec(
                    "BLOCK_CARD",
                    f"R2: customer denies the transaction; exposure ${exposure:,.2f} <= $2,500",
                ),
                P.Rec("CREATE_CASE", "R2, §3a: customer disputes a charge"),
                P.Rec(
                    "ESCALATE_TO_ANALYST",
                    "R8: verdict uncertain - the denial is the only evidence; the card-present purchase shows "
                    "no graph anomaly to confirm or refute it",
                ),
            ]
            return _pack(
                "uncertain",
                "escalated",
                p0,
                episode,
                exposure,
                [],
                recs,
                recs,
                [],
                "The cardholder's own statement is already the verification response (§6: a verification "
                "response settles what it can); the graph has nothing further to add, so the case goes to an "
                "analyst under R8.",
                "nothing",
            )
        if p_final >= P.R1_BLOCK_MIN_PROBABILITY:
            return _fraud_final(
                case,
                sig,
                pattern,
                p_final,
                episode,
                exposure,
                connected,
                coordinated,
                initial=None,
                requests=[],
                reason_prefix="R2: customer denies the transaction",
                amounts=amounts,
                stop="The customer's denial plus graph evidence put the probability at "
                f"{p_final}; a further request would not change the action (§6).",
            )
        # Denial conflicts with the graph evidence -> R1 + R8
        initial = [
            P.Rec("CREATE_CASE", "§3a: customer disputes a charge"),
            P.Rec(
                "STEP_UP_AUTH",
                f"R1: denial is the only fraud signal and probability {p0} < 0.70; "
                "verify before blocking",
            ),
            P.Rec("MONITOR_CARD", "R1: keep watching while verification is pending"),
        ]
        if exposure > P.R8_ESCALATE_MIN_EXPOSURE or a0.legit_groups:
            initial.append(
                P.Rec("ESCALATE_TO_ANALYST", "R8: evidence conflicts with the customer's denial")
            )
        fav = p0 >= 0.5
        requests.append(
            {
                "type": "step_up_auth",
                "asked_after_step": assess_step,
                "assumed_response": (
                    "Step-up authentication on the account fails and the customer repeats the denial (simulated: "
                    f"probability {p0} favoured fraud)."
                    if fav
                    else "Customer passes step-up authentication and, shown the merchant details, recognises the purchase "
                    f"(simulated: probability {p0} favoured legitimate)."
                ),
            }
        )
        if fav:
            p_final = round(_sigmoid(_logit(p0) + DENIAL_LOGODDS), 2)
            return _fraud_final(
                case,
                sig,
                pattern,
                p_final,
                episode,
                exposure,
                connected,
                coordinated,
                initial=initial,
                requests=requests,
                reason_prefix="R2: customer denial confirmed",
                amounts=amounts,
                stop="Step-up result settled the question (§6).",
            )
        p_final = round(_sigmoid(_logit(p0) + CONFIRMATION_LOGODDS), 2)
        final = [
            P.Rec("CREATE_CASE", "§3a: the dispute is recorded"),
            P.Rec("CLOSE_NO_FRAUD", "R3: customer recognised the purchase after step-up"),
        ]
        return _pack(
            "legitimate",
            "closed_legitimate",
            p_final,
            [],
            0.0,
            [],
            initial,
            final,
            requests,
            "Step-up result settled the question (§6).",
            "The simulated step-up pass and recognition moved the case from verify-and-monitor to closed "
            "as legitimate.",
        )

    # ---------------------------------------------------------- model / analyst trigger
    n_ind = len(a0.fraud_groups)
    if p0 >= P.STOP_HIGH and n_ind >= P.STOP_MIN_INDEPENDENT:
        return _fraud_final(
            case,
            sig,
            pattern,
            p0,
            episode,
            exposure,
            connected,
            coordinated,
            initial=None,
            requests=[],
            reason_prefix=f"§6: probability {p0} on {n_ind} independent lines of evidence",
            amounts=amounts,
            stop=f"Probability {p0} >= 0.85 on {n_ind} independent lines of evidence "
            f"({', '.join(a0.fraud_groups)}); stopping per §6.",
        )
    if (
        p0 <= P.STOP_LOW
        and len(a0.legit_groups) + (0 if a0.fraud_groups else 1) >= P.STOP_MIN_INDEPENDENT
    ):
        final = [
            P.Rec("ALLOW_TRANSACTION", f"§6: probability {p0} <= 0.15"),
            P.Rec("CLOSE_NO_FRAUD", "§6: no fraud signal; alert closed as legitimate"),
        ]
        return _pack(
            "legitimate",
            "closed_legitimate",
            p0,
            [],
            0.0,
            [],
            final,
            final,
            [],
            f"Probability {p0} <= 0.15 with supporting evidence ({', '.join(a0.legit_groups) or 'no fraud signal'}); "
            "stopping per §6.",
            "nothing",
        )

    # uncertain middle band -> R1 verify first (+ R5 / DECLINE if strong)
    if sig.on("card_testing_sequence"):
        initial.append(P.Rec("DECLINE_TRANSACTION", "R5: testing sequence observed"))
        initial.append(P.Rec("STEP_UP_AUTH", "R5: testing sequence observed"))
    elif p0 >= P.R1_BLOCK_MIN_PROBABILITY:
        initial.append(
            P.Rec(
                "DECLINE_TRANSACTION",
                f"§3b, R1: probability {p0}; hold the flagged authorization while verification is pending",
            )
        )
    initial.append(
        P.Rec(
            "VERIFY_WITH_CUSTOMER",
            f"R1: probability {p0} rests on {n_ind} line(s) of evidence; confirm before any block"
            if p0 < P.R1_BLOCK_MIN_PROBABILITY or n_ind <= 1
            else f"§3b: probability {p0} is below the 0.85 stopping point; confirm before blocking",
        )
    )
    initial.append(
        P.Rec(
            "CREATE_CASE",
            "§3a: evidence requested" + (f"; probability {p0} >= 0.30" if p0 >= 0.3 else ""),
        )
    )
    if ring:
        initial.append(
            P.Rec(
                "MONITOR_CONNECTED_CARDS",
                f"R6: device profile shared with {len(connected)} other card(s)",
            )
        )

    fav = p0 >= 0.5
    requests.append(
        {
            "type": "customer_validation",
            "asked_after_step": assess_step,
            "assumed_response": (
                "Customer states they did not make the transaction and still holds the card (simulated: "
                f"probability {p0} favoured fraud)."
                if fav
                else _confirmation_text(sig, pattern)
                + f" (simulated: probability {p0} favoured legitimate)."
            ),
        }
    )
    if fav:
        p1 = round(_sigmoid(_logit(p0) + DENIAL_LOGODDS), 2)
        return _fraud_final(
            case,
            sig,
            pattern,
            p1,
            episode,
            exposure,
            connected,
            coordinated,
            initial=initial,
            requests=requests,
            reason_prefix="R2: customer denied",
            amounts=amounts,
            stop="The customer's (simulated) denial settled the question (§6).",
        )
    p1 = round(_sigmoid(_logit(p0) + CONFIRMATION_LOGODDS), 2)
    final = [
        P.Rec(
            "CLOSE_NO_FRAUD",
            "R3: customer confirmed the transaction; confirmation noted in the case",
        )
    ]
    if p0 >= P.CASE_MIN_PROBABILITY or requests:
        final.insert(
            0, P.Rec("CREATE_CASE", "§3a: case records the verification and closes as legitimate")
        )
    return _pack(
        "legitimate",
        "closed_legitimate",
        p1,
        [],
        0.0,
        [],
        initial,
        final,
        requests,
        "The customer's (simulated) confirmation settled the question (§6).",
        f"Customer confirmation dropped the probability from {p0} to {p1}; the verify request became a "
        "no-fraud closure (R3).",
    )


def _confirmation_text(sig: SignalSet, pattern: str) -> str:
    if sig.on("new_billing_region") or pattern == "out_of_region_use":
        return "Customer confirms they were travelling in that billing region and made the purchase"
    if sig.on("new_device"):
        return "Customer confirms the purchase was made from their new phone"
    return "Customer confirms they made the purchase"


def _fraud_final(
    case,
    sig,
    pattern,
    p,
    episode,
    exposure,
    connected,
    coordinated,
    *,
    initial,
    requests,
    reason_prefix,
    stop,
    amounts=(),
) -> dict:
    ring = sig.on("shared_device_ring")
    final: list[P.Rec] = []
    if sig.on("card_testing_sequence"):
        cleared_big = any(a > P.R5_BLOCK_CLEARED_PURCHASE for a in amounts)
        if cleared_big:
            final.append(
                P.Rec(
                    "BLOCK_CARD",
                    f"R5, {reason_prefix}: a purchase over $100 has already cleared; "
                    f"exposure ${exposure:,.2f}",
                )
            )
        else:
            final += [
                P.Rec("DECLINE_TRANSACTION", f"R5: {reason_prefix}"),
                P.Rec("STEP_UP_AUTH", "R5"),
            ]
    else:
        final.append(
            P.Rec(
                "BLOCK_CARD",
                f"{reason_prefix}; exposure ${exposure:,.2f} "
                f"{'<=' if exposure <= P.BLOCK_CARD_L1_MAX_EXPOSURE else '>'} $2,500",
            )
        )
    final.append(P.Rec("CREATE_CASE", "R2, §3a"))
    sar, _ = P.sar_required(
        "fraud", p, exposure, shared_origin=ring, undocumented_or_coordinated=coordinated
    )
    if sar:
        final.append(
            P.Rec(
                "FILE_REPORT",
                "R6/R9 shared or coordinated origin"
                if (ring or coordinated)
                else f"R2: exposure ${exposure:,.2f} exceeds $1,000",
            )
        )
    if connected:
        final.append(
            P.Rec(
                "MONITOR_CONNECTED_CARDS",
                f"R6: {len(connected)} other card(s) share the device profile",
            )
        )
    if coordinated:
        final.append(P.Rec("ESCALATE_TO_ANALYST", "R9: undocumented, coordinated pattern"))
    initial = initial if initial is not None else final
    what = (
        "nothing"
        if initial is final
        else f"The (simulated) customer response moved the probability to {p}, turning verification into "
        f"{', '.join(r.action for r in final if r.action not in {i.action for i in initial}) or 'the same actions'}."
    )
    return _pack(
        "fraud",
        "escalated" if coordinated else "closed_fraud",
        p,
        episode,
        exposure,
        connected,
        initial,
        final,
        requests,
        stop,
        what,
    )


def _pack(
    verdict, status, p, affected, exposure, connected, initial, final, requests, stop, what
) -> dict:
    initial = P.dedupe(initial)
    final = P.dedupe(final)
    return {
        "verdict": verdict,
        "status": status,
        "final_probability": p,
        "affected": list(affected),
        "exposure": round(exposure, 2),
        "connected_cards": list(connected),
        "initial": [r.as_dict(exposure) for r in initial],
        "final": [r.as_dict(exposure) for r in final],
        "evidence_requests": requests,
        "stop_reason": stop,
        "what_changed": "nothing" if not requests else what,
    }


# ------------------------------------------------------------------ evidence list


def _evidence(
    case, flagged, sig, a0, decision, device, own_closed, similar_ids, memory
) -> list[dict]:
    ev = [
        {
            "claim": f"Alert: {case.trigger_text}",
            "source": "customer" if case.trigger_type == "customer_report" else "external",
            "ref": f"case_pack:{case.case_id}",
            "entity_ids": [case.flagged_txn_id, case.card_id],
        }
    ]
    ref_for = {
        "new_device": "query:card_history+identity(device_status)",
        "proxy": "query:get_transaction(proxy_type)",
        "shared_device_ring": "query:device_neighbours(window=30d)",
        "device_in_confirmed_fraud": "query:device_neighbours->H_INVOLVES->HClosedCase",
    }
    for name, s in sig.signals.items():
        if s.present and name != "history_depth":
            ev.append(
                {
                    "claim": s.detail[0].upper() + s.detail[1:],
                    "source": "graph",
                    "ref": ref_for.get(name, f"query:card_history(card_id={case.card_id})"),
                    "entity_ids": [e for e in s.entity_ids if e][:12],
                }
            )
    if device and not sig.on("shared_device_ring"):
        ev.append(
            {
                "claim": f"Device profile seen on {len(device['cards'])} card(s) within 30 days; no ring",
                "source": "graph",
                "ref": "query:device_neighbours(window=30d)",
                "entity_ids": [c["card_id"] for c in device["cards"]][:6],
            }
        )
    if own_closed:
        ev.append(
            {
                "claim": "Customer's cards have prior closed cases: "
                + ", ".join(
                    f"{c['case_id']} ({c['outcome']}, {c['pattern']})" for c in own_closed[:4]
                ),
                "source": "graph",
                "ref": "query:closed_cases_for_cards",
                "entity_ids": [c["case_id"] for c in own_closed[:4]],
            }
        )
    if similar_ids:
        ev.append(
            {
                "claim": f"Retrieved closed cases used as memory: {', '.join(similar_ids)}",
                "source": "graph",
                "ref": "query:similar_closed_cases",
                "entity_ids": similar_ids,
            }
        )
    if memory:
        ev.append(
            {
                "claim": "Earlier agent investigations linked by card or device: "
                + ", ".join(m["graph_case_id"] for m in memory[:5]),
                "source": "graph",
                "ref": "query:case_memory",
                "entity_ids": [m["official_case_id"] for m in memory[:5]],
            }
        )
    ev.append(
        {
            "claim": f"Assessed fraud probability {a0.probability} before further evidence (lines of evidence: "
            f"{', '.join(a0.fraud_groups) or 'none'}; legitimate indicators: {', '.join(a0.legit_groups) or 'none'})",
            "source": "document",
            "ref": "policy:§6,R1",
            "entity_ids": [case.flagged_txn_id],
        }
    )
    for i, r in enumerate(decision["evidence_requests"], 1):
        ev.append(
            {
                "claim": r["assumed_response"],
                "source": "customer",
                "ref": f"evidence_request:{i}",
                "entity_ids": [],
            }
        )
    return ev
