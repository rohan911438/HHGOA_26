"""Offline tests for the official HHGOA_IEEE benchmark package.

No TigerGraph, no dataset files: policy routing, SAR rule, pattern
signals on synthetic card histories, answer conformance, and the
no-fallback guarantee when the official package is absent.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.benchmark.official import policy as P
from app.benchmark.official.agent import assess, classify, decide
from app.benchmark.official.dataset import (
    OfficialCase,
    OfficialDatasetUnavailable,
    load_official_cases,
    validate_official_package,
)
from app.benchmark.official.graph_load import device_profile
from app.benchmark.official.signals import compute_signals
from app.benchmark.official.validate import check_answer

# The dataset timestamps are naive (no zone), so the fixtures are too.
T0 = datetime(2016, 11, 20, 12, 0, 0)  # noqa: DTZ001


def txn(i, minutes, amount, channel="online", **kw):
    base = {
        "txn_id": str(i),
        "ts": (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S"),
        "amount": amount,
        "channel": channel,
        "product_cd": "W" if channel == "in_person" else "C",
        "addr1": "204.0",
        "device_profile": "",
        "device_status": "",
        "proxy_type": "",
        "card_id": "C00001-K1",
        "customer_id": "C00001",
        "p_email": "gmail.com",
    }
    base.update(kw)
    return base


def history(n=20, channel="in_person", amount=40.0):
    return [txn(1000 + k, -60 * 24 * (k + 3), amount, channel) for k in range(n)]


def case(trigger="risk_score", score=0.6):
    return OfficialCase(
        case_id="HHG-999",
        opened_at="2016-11-20 12:00:00",
        trigger_type=trigger,
        trigger_text="x",
        flagged_txn_id="9",
        card_id="C00001-K1",
        customer_id="C00001",
        risk_score=score if trigger == "risk_score" else None,
    )


# ------------------------------------------------------------------ policy §2


@pytest.mark.parametrize(
    ("action", "exposure", "expected"),
    [
        ("VERIFY_WITH_CUSTOMER", 0, "auto"),
        ("CREATE_CASE", 9999, "auto"),
        ("DECLINE_TRANSACTION", 10, "L1"),
        ("BLOCK_CARD", 2500, "L1"),
        ("BLOCK_CARD", 2500.01, "L2"),
        ("BLOCK_ALL_CARDS", 1, "L2"),
        ("FILE_REPORT", 1, "L2"),
    ],
)
def test_routes_follow_policy_section_2(action, exposure, expected):
    assert P.route(action, exposure) == expected


def test_unknown_action_rejected():
    with pytest.raises(ValueError):
        P.route("FREEZE_ACCOUNT")


def test_sar_rule_section_3a():
    assert P.sar_required(
        "fraud", 0.9, 1500, shared_origin=False, undocumented_or_coordinated=False
    )[0]
    assert P.sar_required("fraud", 0.9, 200, shared_origin=True, undocumented_or_coordinated=False)[
        0
    ]
    assert not P.sar_required(
        "fraud", 0.9, 200, shared_origin=False, undocumented_or_coordinated=False
    )[0]
    assert not P.sar_required(
        "legitimate", 0.1, 5000, shared_origin=True, undocumented_or_coordinated=True
    )[0]
    # "confirmed or strongly suspected" - a weak suspicion never files
    assert not P.sar_required(
        "fraud", 0.5, 5000, shared_origin=False, undocumented_or_coordinated=False
    )[0]


# ------------------------------------------------------------------ signals


def test_card_testing_sequence_detected():
    h = history()
    run = [txn(1, 0, 1.1), txn(2, 10, 2.4), txn(3, 25, 0.95), txn(4, 70, 259.98)]
    sig = compute_signals(run[3], h + run)
    assert sig.on("card_testing_sequence")
    assert sig.episode_txn_ids == ["1", "2", "3", "4"]
    assert classify(case(), sig, run[3])[0] == "card_testing"


def test_sub_threshold_structuring_is_undocumented():
    h = history()
    run = [txn(1, 0, 489.0), txn(2, 12, 495.5), txn(3, 25, 478.0), txn(4, 38, 497.0)]
    sig = compute_signals(run[1], h + run)
    assert sig.on("sub_threshold_structuring")
    pattern, desc = classify(case(), sig, run[1])
    assert pattern == "undocumented" and len(desc) > 40


def test_trip_is_not_out_of_region_fraud():
    h = history()
    trip = [txn(10 + d, 60 * 24 * d, 30.0, "in_person", addr1="444.0") for d in range(4)]
    sig = compute_signals(trip[0], h + trip)
    assert sig.on("new_billing_region") and sig.on("multi_day_stay")
    assert classify(case(), sig, trip[0])[0] != "out_of_region_use"


def test_out_of_region_with_home_activity():
    h = history()
    away = txn(10, 0, 111.9, "in_person", addr1="264.0")
    home = txn(11, 60, 25.0, "in_person", addr1="204.0")
    sig = compute_signals(away, [*h, away, home])
    assert sig.on("new_billing_region") and sig.on("home_activity_continues")
    assert classify(case(), sig, away)[0] == "out_of_region_use"


def test_recurring_charge_detected():
    h = [txn(100 + k, -60 * 24 * 30 * (k + 1), 59.67, "online") for k in range(4)]
    flagged = txn(9, 0, 59.67, "online")
    assert compute_signals(flagged, [*h, flagged]).on("recurring_charge")


def test_shared_device_ring():
    flagged = txn(
        9,
        0,
        120.0,
        device_profile="SM-G935F | Android 7.0 | chrome | 1920x1080",
        device_status="New",
    )
    dev = {
        "cards": [
            {"card_id": "C00001-K1"},
            {"card_id": "C00002-K1"},
            {"card_id": "C00003-K2"},
            {"card_id": "C00004-K1"},
        ],
        "confirmed_fraud_cases": ["CC-2649"],
        # fingerprint of the bank's documented ring device
        "all_txns": 9,
        "all_cards": 4,
        "new_share_all": 1.0,
    }
    sig = compute_signals(flagged, [*history(), flagged], dev)
    assert sig.on("shared_device_ring")
    assert sig.ring_cards == ["C00002-K1", "C00003-K2", "C00004-K1"]


def test_popular_device_profile_is_not_a_ring():
    flagged = txn(
        9,
        0,
        120.0,
        device_profile="Windows | Windows 10 | chrome 63.0 | 1920x1080",
        device_status="New",
    )
    dev = {
        "cards": [{"card_id": f"C{i:05d}-K1"} for i in range(2, 200)],
        "confirmed_fraud_cases": ["CC-0001", "CC-0002"],
        "all_txns": 1500,
        "all_cards": 600,
        "new_share_all": 0.35,
    }
    sig = compute_signals(flagged, [*history(), flagged], dev)
    assert not sig.on("shared_device_ring")
    assert not sig.on("device_in_confirmed_fraud")
    assert sig.ring_cards == []
    assert not sig.linkable_device


def test_transparent_proxy_is_not_suspicious():
    flagged = txn(9, 0, 50.0, device_profile="x | y | z | w", proxy_type="IP_PROXY:TRANSPARENT")
    assert not compute_signals(flagged, [*history(), flagged]).on("proxy")
    flagged["proxy_type"] = "IP_PROXY:ANONYMOUS"
    assert compute_signals(flagged, [*history(), flagged]).on("proxy")


def test_device_profile_format_matches_readme_example():
    assert (
        device_profile(
            "SAMSUNG SM-G892A Build/NRD90M", "Android 7.0", "samsung browser 6.2", "2220x1080"
        )
        == "SAMSUNG SM-G892A Build/NRD90M | Android 7.0 | samsung browser 6.2 | 2220x1080"
    )
    assert device_profile(None, float("nan"), "", None) == ""


# ------------------------------------------------------------------ decisions


def test_weak_single_signal_verifies_before_block_r1():
    h = history(channel="online", amount=100.0)
    flagged = txn(9, 0, 100.07, "online")
    c = case(score=0.54)
    sig = compute_signals(flagged, [*h, flagged])
    a0 = assess(c, sig)
    d = decide(c, sig, classify(c, sig, flagged)[0], a0, flagged, {"9": flagged}, 6)
    initial = [r["action"] for r in d["initial"]]
    assert "BLOCK_CARD" not in initial
    assert "VERIFY_WITH_CUSTOMER" in initial
    assert d["evidence_requests"]


def test_recurring_dispute_is_not_blocked_r7():
    h = [txn(100 + k, -60 * 24 * 30 * (k + 1), 59.67, "online") for k in range(4)]
    flagged = txn(9, 0, 59.67, "online")
    c = case("customer_report")
    sig = compute_signals(flagged, [*h, flagged])
    d = decide(c, sig, "none", assess(c, sig), flagged, {"9": flagged}, 6)
    acts = {r["action"] for r in d["initial"] + d["final"]}
    assert {"CREATE_CASE", "VERIFY_WITH_CUSTOMER", "WARN_CUSTOMER"} <= acts
    assert "BLOCK_CARD" not in acts
    assert d["verdict"] == "legitimate"


def test_routes_are_attached_from_policy():
    h = history()
    run = [txn(1, 0, 1.1), txn(2, 10, 2.4), txn(3, 25, 0.95), txn(4, 70, 259.98)]
    c = case(score=0.9)
    sig = compute_signals(run[3], h + run)
    by_id = {t["txn_id"]: t for t in run}
    d = decide(c, sig, "card_testing", assess(c, sig), run[3], by_id, 6)
    for r in d["initial"] + d["final"]:
        assert r["route"] == P.route(r["action"], d["exposure"])


# ------------------------------------------------------------------ conformance


def _answer(**over):
    a = {
        "case_id": "HHG-999",
        "case": {
            "status": "closed_legitimate",
            "verdict": "legitimate",
            "fraud_probability": 0.1,
            "pattern": "none",
            "pattern_description": "",
            "affected_txn_ids": [],
            "first_suspicious_txn_id": "",
            "connected_card_ids": [],
            "connected_device_profiles": [],
            "exposure_usd": 0,
            "evidence": [{"claim": "x", "source": "graph", "ref": "q", "entity_ids": ["9"]}],
            "similar_prior_cases": [],
            "summary": "s",
            "written_to_graph": True,
            "graph_case_id": "CASE-HHG-999",
        },
        "evidence_requests": [],
        "next_best_actions": {
            "initial": [{"action": "CLOSE_NO_FRAUD", "route": "auto", "reason": "§6"}],
            "final": [{"action": "CLOSE_NO_FRAUD", "route": "auto", "reason": "§6"}],
            "what_changed": "nothing",
        },
        "sar": {
            "file": False,
            "reason": "r",
            "narrative": "",
            "subjects": [],
            "total_amount_usd": 0,
            "activity_dates": [],
        },
        "stop_reason": "s",
        "tool_calls": 3,
        "tokens": 0,
        "latency_s": 1.0,
    }
    a.update(over)
    return a


REFS = {
    "txn_amounts": {"9": 10.0},
    "card_ids": {"C00001-K1"},
    "customer_ids": {"C00001"},
    "closed_case_ids": {"CC-0001"},
    "device_profiles": set(),
}


def test_conformant_answer_passes():
    failed = [c for c in check_answer(_answer(), **REFS) if not c["ok"]]
    assert failed == []


def test_wrong_route_and_sar_mismatch_are_caught():
    bad = _answer(
        next_best_actions={
            "initial": [{"action": "BLOCK_CARD", "route": "auto", "reason": "R2"}],
            "final": [{"action": "FILE_REPORT", "route": "L2", "reason": "R2"}],
            "what_changed": "x",
        }
    )
    failed = {c["check"] for c in check_answer(bad, **REFS) if not c["ok"]}
    assert "initial_routes_match_policy_s2" in failed
    assert "sar_file_matches_FILE_REPORT" in failed


def test_made_up_ids_are_caught():
    bad = _answer()
    bad["case"]["similar_prior_cases"] = ["CC-9999"]
    failed = {c["check"] for c in check_answer(bad, **REFS) if not c["ok"]}
    assert "similar_cases_exist" in failed


# ------------------------------------------------------------------ no fallback


def test_missing_package_raises_and_never_falls_back(tmp_path):
    with pytest.raises(OfficialDatasetUnavailable, match="OFFICIAL HHGOA DATASET UNAVAILABLE"):
        load_official_cases(tmp_path / "nope")
    rep = validate_official_package(tmp_path / "nope")
    assert not rep.ok and rep.cases == []


def test_found_device_counts_as_known():
    flagged = txn(9, 0, 30.0, device_profile="a | b | c | d", device_status="Found")
    assert compute_signals(flagged, [*history(), flagged]).on("known_device")


def test_in_person_denial_without_corroboration_is_uncertain_and_escalated():
    flagged = txn(9, 0, 49.0, "in_person")
    c = case("customer_report")
    sig = compute_signals(flagged, [*history(), flagged])
    d = decide(c, sig, "none", assess(c, sig), flagged, {"9": flagged}, 6)
    assert d["verdict"] == "uncertain" and d["status"] == "escalated"
    assert [r["action"] for r in d["final"]] == ["BLOCK_CARD", "CREATE_CASE", "ESCALATE_TO_ANALYST"]
    assert d["evidence_requests"] == []
    assert d["final"] == d["initial"]
