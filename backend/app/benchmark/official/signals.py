"""Pattern signals for one alert, computed from graph-retrieved facts.

Pure functions: they take the card's transaction history (as returned by
the TigerGraph tools in `tools.py`) plus neighbourhood facts, and return
named, explainable signals. No I/O, no LLM, no knowledge of any answer.

Every threshold is taken from the official README's pattern definitions
and Fraud Policy, or from recurring analyst notes in
`closed_cases_history.csv`; the source is cited next to each one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

# README pattern 1 / policy R5: "three or more tiny online authorizations,
# often under $5, then a larger purchase", "within an hour".
TEST_AMOUNT_MAX = 5.0
TEST_MIN_COUNT = 3
TEST_WINDOW = timedelta(hours=1)
# README pattern 2: "a burst of two to four within 48 hours".
BURST_WINDOW = timedelta(hours=48)
# Closed-case analyst notes (undocumented, CC-3748 et al.): "four online
# purchases within forty minutes, each just under $500".
STRUCT_LOW, STRUCT_HIGH = 400.0, 500.0
STRUCT_WINDOW = timedelta(minutes=60)
# README pattern 4: "Several days of purchases in one new region is a trip".
TRIP_MIN_DAYS = 3
HOME_ACTIVITY_WINDOW = timedelta(days=3)
# Closed-case notes (undocumented, CC-2649 et al.): same device profile on
# several other cardholders' fraud "this month". A shared profile alone is
# not a ring: mass-market configurations are shared by hundreds of cards
# (measured: 99th percentile 112 cards per profile). The bank's documented
# ring device (CC-2649/2971/2985/3035) is marked New on 100% of its
# transactions, sits behind an anonymous proxy on 100%, and averages 2.2
# transactions per card - it hops between cards. The ring test uses that
# measured fingerprint.
RING_WINDOW = timedelta(days=30)
RING_MIN_OTHER_CARDS = 2
RING_MIN_NEW_SHARE = 0.8
RING_MAX_TXNS_PER_CARD = 3.0
# 95th percentile of cards per device profile (measured: 22). Above it a
# profile is a mass-market configuration, useless for linking cases.
POPULAR_PROFILE_CARDS = 22
# Policy R7: "same merchant, same amount, monthly". There is no merchant
# column; product code + amount within 2% + 25-35 day spacing stands in.
RECUR_AMOUNT_TOL = 0.02
RECUR_GAP_DAYS = (25, 35)


def parse_ts(v) -> datetime:
    if isinstance(v, datetime):
        return v
    # The dataset's `ts` is a naive calendar time with no zone; kept naive.
    return datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007


@dataclass
class Signal:
    name: str
    present: bool
    detail: str
    entity_ids: list[str] = field(default_factory=list)
    weight: float = 0.0  # log-odds contribution when present, set by the scorer


@dataclass
class SignalSet:
    signals: dict[str, Signal] = field(default_factory=dict)
    episode_txn_ids: list[str] = field(default_factory=list)
    first_txn_id: str = ""
    home_region: str = ""
    shared_device_profile: str = ""
    ring_cards: list[str] = field(default_factory=list)
    linkable_device: bool = False

    def add(self, s: Signal) -> None:
        self.signals[s.name] = s

    def on(self, name: str) -> bool:
        s = self.signals.get(name)
        return bool(s and s.present)


def _amt(t) -> float:
    return float(t["amount"])


def compute_signals(
    flagged: dict, card_txns: list[dict], device_activity: dict | None = None
) -> SignalSet:
    """`flagged`: the alerted transaction. `card_txns`: every transaction on
    the same card (any order). `device_activity`: output of the
    device-neighbour tool for the flagged txn's device profile, or None."""
    out = SignalSet()
    t0 = parse_ts(flagged["ts"])
    fid = str(flagged["txn_id"])
    txns = sorted(card_txns, key=lambda t: parse_ts(t["ts"]))
    before = [t for t in txns if parse_ts(t["ts"]) < t0]
    near = [t for t in txns if abs(parse_ts(t["ts"]) - t0) <= BURST_WINDOW]
    online = flagged.get("channel") == "online"

    # ---- baseline behaviour (history strictly before the alert)
    hist_amounts = [_amt(t) for t in before] or [0.0]
    med = median(hist_amounts)
    hist_products = {t.get("product_cd") for t in before}
    hist_regions = [t.get("addr1") for t in before if t.get("addr1")]
    hist_devices = {t.get("device_profile") for t in before if t.get("device_profile")}
    hist_online = sum(1 for t in before if t.get("channel") == "online")
    out.home_region = max(set(hist_regions), key=hist_regions.count) if hist_regions else ""

    out.add(
        Signal(
            "history_depth", len(before) >= 5, f"{len(before)} prior transactions on this card", []
        )
    )

    # ---- card testing (pattern 1, R5)
    small = [t for t in near if t.get("channel") == "online" and _amt(t) < TEST_AMOUNT_MAX]
    testing_run: list[dict] = []
    for i, s in enumerate(small):
        run = [x for x in small[i:] if parse_ts(x["ts"]) - parse_ts(s["ts"]) <= TEST_WINDOW]
        if len(run) >= TEST_MIN_COUNT:
            testing_run = run
            break
    larger_after = []
    if testing_run:
        last = parse_ts(testing_run[-1]["ts"])
        larger_after = [
            t
            for t in near
            if parse_ts(t["ts"]) > last
            and _amt(t) >= TEST_AMOUNT_MAX
            and parse_ts(t["ts"]) - last <= timedelta(hours=24)
        ]
    ct = bool(testing_run and larger_after)
    out.add(
        Signal(
            "card_testing_sequence",
            ct,
            f"{len(testing_run)} online authorizations under ${TEST_AMOUNT_MAX:.0f} within 1h, then "
            f"{len(larger_after)} larger purchase(s)"
            if testing_run
            else "no run of 3+ sub-$5 online authorizations",
            [str(t["txn_id"]) for t in testing_run + larger_after[:3]],
        )
    )

    # ---- threshold structuring (undocumented, from closed-case notes)
    band = [t for t in near if t.get("channel") == "online" and STRUCT_LOW <= _amt(t) < STRUCT_HIGH]
    struct_run: list[dict] = []
    for i, s in enumerate(band):
        run = [x for x in band[i:] if parse_ts(x["ts"]) - parse_ts(s["ts"]) <= STRUCT_WINDOW]
        if len(run) >= 3:
            struct_run = run
            break
    out.add(
        Signal(
            "sub_threshold_structuring",
            bool(struct_run),
            f"{len(struct_run)} online purchases between ${STRUCT_LOW:.0f} and ${STRUCT_HIGH:.0f} within 60 minutes"
            if struct_run
            else "no cluster of just-under-$500 online purchases",
            [str(t["txn_id"]) for t in struct_run],
        )
    )

    # ---- card-not-present burst (pattern 2)
    online_near = [
        t for t in near if t.get("channel") == "online" and parse_ts(t["ts"]) >= t0 - BURST_WINDOW
    ]
    unusual = [
        t
        for t in online_near
        if (_amt(t) > max(3 * med, med + 50)) or (t.get("product_cd") not in hist_products)
    ]
    out.add(
        Signal(
            "online_amount_or_product_unusual",
            online and any(str(t["txn_id"]) == fid for t in unusual),
            f"flagged ${_amt(flagged):.2f} (product {flagged.get('product_cd')}) vs card median ${med:.2f}; "
            f"product seen before: {flagged.get('product_cd') in hist_products}",
            [fid],
        )
    )
    out.add(
        Signal(
            "online_burst_48h",
            online and 2 <= len(unusual) <= 6,
            f"{len(unusual)} unusual online purchases within 48h of the alert",
            [str(t["txn_id"]) for t in unusual],
        )
    )
    out.add(
        Signal(
            "no_prior_online_use",
            online and hist_online == 0,
            f"{hist_online} prior online transactions on this card",
            [fid],
        )
    )

    # ---- device (pattern 3)
    prof = flagged.get("device_profile") or ""
    new_dev = online and (
        flagged.get("device_status") == "New"
        or (prof and prof not in hist_devices and hist_devices)
    )
    out.add(
        Signal(
            "new_device",
            bool(new_dev),
            f"identity record device status '{flagged.get('device_status') or 'n/a'}'; profile "
            f"{'not ' if prof not in hist_devices else ''}seen earlier on this card",
            [fid],
        )
    )
    out.add(
        Signal(
            "known_device",
            # README: id_15 is the identity record's New / Found device flag
            bool(
                online
                and ((prof and prof in hist_devices) or flagged.get("device_status") == "Found")
            ),
            f"device already known to the account (identity record '{flagged.get('device_status') or 'n/a'}'"
            f"{', profile used on this card before' if prof in hist_devices else ''})",
            [fid],
        )
    )
    proxy = (flagged.get("proxy_type") or "").strip()
    # id_23 values are IP_PROXY:TRANSPARENT / ANONYMOUS / HIDDEN; only the
    # last two conceal the origin.
    out.add(
        Signal(
            "proxy",
            bool(online and proxy and "TRANSPARENT" not in proxy.upper()),
            f"proxy flag (id_23) = '{proxy or 'none'}'",
            [fid],
        )
    )

    # ---- region (pattern 4)
    region = flagged.get("addr1") or ""
    in_person = flagged.get("channel") == "in_person"
    new_region = bool(in_person and region and hist_regions and region not in set(hist_regions))
    region_days = {
        parse_ts(t["ts"]).date()
        for t in txns
        if t.get("addr1") == region and abs(parse_ts(t["ts"]) - t0) <= timedelta(days=10)
    }
    home_active = [
        t
        for t in txns
        if t.get("addr1") == out.home_region
        and region != out.home_region
        and abs(parse_ts(t["ts"]) - t0) <= HOME_ACTIVITY_WINDOW
    ]
    region_txns = [
        t
        for t in txns
        if t.get("addr1") == region and abs(parse_ts(t["ts"]) - t0) <= timedelta(days=10)
    ]
    out.add(
        Signal(
            "new_billing_region",
            new_region,
            f"card-present use in region {region}; home region {out.home_region or 'unknown'}; "
            f"{len(set(hist_regions))} region(s) in history",
            [fid],
        )
    )
    out.add(
        Signal(
            "home_activity_continues",
            new_region and bool(home_active),
            f"{len(home_active)} home-region transactions within 3 days of the alert",
            [str(t["txn_id"]) for t in home_active[:5]],
        )
    )
    out.add(
        Signal(
            "multi_day_stay",
            new_region and len(region_days) >= TRIP_MIN_DAYS,
            f"purchases on {len(region_days)} distinct day(s) in region {region}",
            [],
        )
    )

    # ---- account takeover (pattern 5): mixed channel + match-flag anomaly
    m_flags = [flagged.get(f"m{i}") for i in range(1, 10)]
    hist_m_false = sum(1 for t in before for i in range(1, 10) if t.get(f"m{i}") == "F")
    hist_m_total = sum(1 for t in before for i in range(1, 10) if t.get(f"m{i}") in ("T", "F"))
    m_false_now = sum(1 for m in m_flags if m == "F")
    hist_rate = hist_m_false / hist_m_total if hist_m_total else 0.0
    m_anom = m_false_now >= 2 and hist_rate < 0.25
    channels_near = {t.get("channel") for t in near}
    out.add(
        Signal(
            "match_flag_anomaly",
            m_anom,
            f"{m_false_now} match flags false on the flagged txn vs {hist_rate:.0%} false historically",
            [fid],
        )
    )
    p_hist = {t.get("p_email") for t in before if t.get("p_email")}
    email_change = bool(flagged.get("p_email") and p_hist and flagged.get("p_email") not in p_hist)
    out.add(
        Signal(
            "purchaser_email_change",
            email_change,
            f"purchaser email domain '{flagged.get('p_email') or 'n/a'}' not seen before on this card",
            [fid],
        )
    )
    out.add(
        Signal(
            "mixed_channel_48h",
            channels_near >= {"online", "in_person"},
            f"channels within 48h: {sorted(c for c in channels_near if c)}",
            [],
        )
    )

    # ---- recurring charge (R7)
    same = [
        t
        for t in before
        if t.get("product_cd") == flagged.get("product_cd")
        and abs(_amt(t) - _amt(flagged)) <= RECUR_AMOUNT_TOL * max(_amt(flagged), 1)
    ]
    gaps = [
        (parse_ts(b["ts"]) - parse_ts(a["ts"])).days
        for a, b in zip(same, same[1:] + [flagged], strict=False)
    ]
    monthly = sum(1 for g in gaps if RECUR_GAP_DAYS[0] <= g <= RECUR_GAP_DAYS[1])
    out.add(
        Signal(
            "recurring_charge",
            len(same) >= 2 and monthly >= 2,
            f"{len(same)} earlier charges of ~${_amt(flagged):.2f} (product {flagged.get('product_cd')}), "
            f"{monthly} at monthly spacing",
            [str(t["txn_id"]) for t in same[-4:]],
        )
    )

    # ---- shared device across other cardholders (R6 / R9)
    if device_activity:
        others = [
            c for c in device_activity.get("cards", []) if c["card_id"] != flagged.get("card_id")
        ]
        other_customers = {c["card_id"].split("-")[0] for c in others} - {
            flagged.get("customer_id")
        }
        fraud_cases = device_activity.get("confirmed_fraud_cases", [])
        all_txns = device_activity.get("all_txns", 0) or 0
        all_cards = device_activity.get("all_cards", 0) or 0
        per_card = all_txns / all_cards if all_cards else 0.0
        new_share = device_activity.get("new_share_all", 0.0)
        ring = (
            len(other_customers) >= RING_MIN_OTHER_CARDS
            and new_share >= RING_MIN_NEW_SHARE
            and per_card <= RING_MAX_TXNS_PER_CARD
        )
        popular = all_cards > POPULAR_PROFILE_CARDS
        out.ring_cards = sorted(c["card_id"] for c in others) if ring else []
        out.linkable_device = bool(prof) and (ring or not popular)
        if ring:
            out.shared_device_profile = prof
        out.add(
            Signal(
                "shared_device_ring",
                ring,
                f"device profile used by {len(other_customers)} other customer(s) within 30 days "
                f"({all_cards} cards all-time, {per_card:.1f} txns per card, marked New on "
                f"{new_share:.0%} of its transactions)"
                + ("; a mass-market configuration, not a ring" if popular and not ring else "")
                + f"; {len(fraud_cases)} confirmed-fraud closed case(s) involve it",
                [*out.ring_cards[:10], *fraud_cases[:5]],
            )
        )
        out.add(
            Signal(
                "device_in_confirmed_fraud",
                # a mass-market profile accumulates unrelated closed cases
                bool(fraud_cases) and not popular,
                f"closed cases on this device profile: {fraud_cases[:5]}",
                fraud_cases[:5],
            )
        )

    # ---- fraud episode: which transactions belong together
    episode: list[dict] = [flagged]
    if ct:
        episode = testing_run + larger_after
    elif struct_run:
        episode = struct_run
    elif out.on("online_burst_48h"):
        episode = unusual
    elif new_region:
        episode = [t for t in region_txns if t.get("channel") == "in_person"] or [flagged]
    if str(fid) not in {str(t["txn_id"]) for t in episode}:
        episode.append(flagged)
    episode = sorted(
        {str(t["txn_id"]): t for t in episode}.values(), key=lambda t: parse_ts(t["ts"])
    )
    out.episode_txn_ids = [str(t["txn_id"]) for t in episode]
    out.first_txn_id = out.episode_txn_ids[0] if episode else fid
    return out
