"""The official HHGOA Fraud Policy v1.0 (README "# Fraud Policy").

Action names and routes are the exact identifiers from the policy. This is
a separate module from `app/policy/engine.py` (the development engine,
whose action vocabulary predates the official policy and is left as is).
"""

from __future__ import annotations

from dataclasses import dataclass

# §1 Actions
ACTIONS = (
    "ALLOW_TRANSACTION",
    "DECLINE_TRANSACTION",
    "MONITOR_CARD",
    "MONITOR_CONNECTED_CARDS",
    "WARN_CUSTOMER",
    "VERIFY_WITH_CUSTOMER",
    "STEP_UP_AUTH",
    "BLOCK_CARD",
    "BLOCK_ALL_CARDS",
    "GENERATE_REPORT",
    "CREATE_CASE",
    "FILE_REPORT",
    "ESCALATE_TO_ANALYST",
    "CLOSE_NO_FRAUD",
)

# §2 Approval routing
AUTO_ACTIONS = {
    "ALLOW_TRANSACTION",
    "MONITOR_CARD",
    "MONITOR_CONNECTED_CARDS",
    "WARN_CUSTOMER",
    "VERIFY_WITH_CUSTOMER",
    "STEP_UP_AUTH",
    "GENERATE_REPORT",
    "CREATE_CASE",
    "ESCALATE_TO_ANALYST",
    "CLOSE_NO_FRAUD",
}
BLOCK_CARD_L1_MAX_EXPOSURE = 2500.0

# §3 thresholds
R1_BLOCK_MIN_PROBABILITY = 0.70
R2_REPORT_MIN_EXPOSURE = 1000.0
R4_ESCALATE_MIN_EXPOSURE = 500.0
R5_BLOCK_CLEARED_PURCHASE = 100.0
R8_ESCALATE_MIN_EXPOSURE = 500.0
CASE_MIN_PROBABILITY = 0.30  # §3a
STOP_HIGH, STOP_LOW = 0.85, 0.15  # §6
STOP_MIN_INDEPENDENT = 2  # §6


def route(action: str, exposure_usd: float = 0.0) -> str:
    """§2. The only place a route is decided."""
    if action not in ACTIONS:
        raise ValueError(f"not a policy action: {action}")
    if action in AUTO_ACTIONS:
        return "auto"
    if action == "DECLINE_TRANSACTION":
        return "L1"
    if action == "BLOCK_CARD":
        return "L1" if exposure_usd <= BLOCK_CARD_L1_MAX_EXPOSURE else "L2"
    return "L2"  # BLOCK_ALL_CARDS, FILE_REPORT


@dataclass
class Rec:
    action: str
    reason: str

    def as_dict(self, exposure_usd: float) -> dict:
        return {
            "action": self.action,
            "route": route(self.action, exposure_usd),
            "reason": self.reason,
        }


def dedupe(recs: list[Rec]) -> list[Rec]:
    """Keeps first occurrence (policy §1: order by what happens first)."""
    seen: set[str] = set()
    out = []
    for r in recs:
        if r.action not in seen:
            seen.add(r.action)
            out.append(r)
    return out


def sar_required(
    verdict: str,
    probability: float,
    exposure_usd: float,
    *,
    shared_origin: bool,
    undocumented_or_coordinated: bool,
) -> tuple[bool, str]:
    """§3a: fraud confirmed or strongly suspected AND at least one of
    exposure > $1,000 / shared device-region-other customer's fraud /
    coordinated or undocumented pattern."""
    if verdict != "fraud" or probability < R1_BLOCK_MIN_PROBABILITY:
        return (
            False,
            "§3a: fraud is not confirmed or strongly suspected, so no report is filed; the case is the record.",
        )
    reasons = []
    if exposure_usd > R2_REPORT_MIN_EXPOSURE:
        reasons.append(f"exposure ${exposure_usd:,.2f} exceeds $1,000 (R2, §3a)")
    if shared_origin:
        reasons.append(
            "activity connects to a shared device profile used on other customers' cards (R2, R6, §3a)"
        )
    if undocumented_or_coordinated:
        reasons.append("pattern is coordinated/undocumented (R9, §3a)")
    if not reasons:
        return False, (
            f"§3a: fraud is confirmed but exposure ${exposure_usd:,.2f} is at or below $1,000 and no shared "
            "origin or coordinated pattern was found; case only, no report."
        )
    return True, "; ".join(reasons)
