"""Phase 2C - the six approved investigation tools.

Each tool wraps exactly one Phase 2A query function
(app.tigergraph.queries) and exactly one Phase 2B normalizer
(app.evidence.normalize) - no new graph-querying or evidence-shaping
logic lives here. This module's only job is the mapping

    validated input -> Phase 2A query call -> Phase 2B Evidence

plus the static metadata a future LangGraph agent needs to choose a
tool. Execution orchestration (timeouts, error normalization, logging)
lives in registry.py, not here - a tool's `run` callable is a plain,
synchronous function that lets exceptions propagate.

ALLOWED_INVESTIGATION_TOOLS is the explicit, hand-written allowlist -
the security boundary. It is a fixed tuple of six InvestigationTool
instances built directly in this module, never assembled by reflecting
over this module's own namespace or over the TigerGraph MCP server's 37
read-only tools (app/mcp/config.py). No raw-GSQL / run-query /
execute-arbitrary-query tool is defined anywhere in this package.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.evidence.models import Evidence, EvidenceType
from app.evidence.normalize import (
    normalize_network_pattern,
    normalize_shared_email,
    normalize_shared_entity,
    normalize_transaction_context,
)
from app.investigation.schemas import (
    NetworkPatternInput,
    SharedAddressInput,
    SharedCardInput,
    SharedDeviceInput,
    SharedEmailInput,
    TransactionContextInput,
    TransactionIdInput,
)
from app.tigergraph import queries
from app.tigergraph.client import TigerGraphClient

# Phase 2A's own measured observation (docs/phase-2-graph-analysis.md):
# interpreted-query latency runs 0.3-2.6s per call. Not yet installed/
# compiled queries, so every tool here carries the same coarse tier -
# a real number, not an invented one, and not a per-tool distinction
# this project hasn't actually measured yet.
_OBSERVED_LATENCY_NOTE = (
    "0.3-2.6s per call observed for interpreted queries "
    "(docs/phase-2-graph-analysis.md); queries are not yet installed/compiled."
)


@dataclass(frozen=True)
class ToolMetadata:
    """What a future LangGraph agent sees about a tool before calling it.
    Describes what evidence the tool returns - never claims the tool
    "detects fraud" (see the six `description` strings below)."""

    name: str
    description: str
    source_query: str  # the app.tigergraph.queries function this wraps
    primary_evidence_type: EvidenceType
    read_only: bool = True
    requires_transaction_id: bool = True
    evidence_types: tuple[str, ...] = ()
    estimated_cost: str = "MEDIUM"  # LOW/MEDIUM/HIGH - PROJECT-DERIVED, not a numeric estimate
    observed_latency_note: str = _OBSERVED_LATENCY_NOTE

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "read_only": self.read_only,
            "requires_transaction_id": self.requires_transaction_id,
            "evidence_types": list(self.evidence_types),
            "estimated_cost": self.estimated_cost,
            "observed_latency_note": self.observed_latency_note,
        }


@dataclass(frozen=True)
class InvestigationTool:
    metadata: ToolMetadata
    input_schema: type[TransactionIdInput]
    run: Callable[[TigerGraphClient, TransactionIdInput], Evidence]

    @property
    def name(self) -> str:
        return self.metadata.name


# ---------------------------------------------------------------- tool bodies


def _run_transaction_context(client: TigerGraphClient, inp: TransactionContextInput) -> Evidence:
    ctx = queries.get_transaction_context(client, inp.transaction_id)
    return normalize_transaction_context(ctx)


def _run_shared_card(client: TigerGraphClient, inp: SharedCardInput) -> Evidence:
    result = queries.find_shared_card_activity(client, inp.transaction_id)
    return normalize_shared_entity(result)


def _run_shared_device(client: TigerGraphClient, inp: SharedDeviceInput) -> Evidence:
    result = queries.find_shared_device_activity(client, inp.transaction_id)
    return normalize_shared_entity(result)


def _run_shared_address(client: TigerGraphClient, inp: SharedAddressInput) -> Evidence:
    result = queries.find_shared_address_activity(client, inp.transaction_id)
    return normalize_shared_entity(result)


def _run_shared_email(client: TigerGraphClient, inp: SharedEmailInput) -> Evidence:
    result = queries.find_shared_email_activity(client, inp.transaction_id)
    return normalize_shared_email(result)


def _run_network_pattern(client: TigerGraphClient, inp: NetworkPatternInput) -> Evidence:
    result = queries.investigate_transaction_network(client, inp.transaction_id)
    return normalize_network_pattern(result)


# ---------------------------------------------------------------- the six tools


tool_get_transaction_context = InvestigationTool(
    metadata=ToolMetadata(
        name="get_transaction_context",
        description=(
            "Return a transaction's own attributes plus what is directly linked to it "
            "(card, address, purchaser/recipient email domain, device). Useful as the "
            "first call in an investigation, to see what entities exist to investigate "
            "further. Includes the dataset's binary fraud label as a labeled fact - never "
            "as a probability or confidence score."
        ),
        source_query="get_transaction_context",
        primary_evidence_type=EvidenceType.TRANSACTION_CONTEXT,
        evidence_types=("transaction_context",),
    ),
    input_schema=TransactionContextInput,
    run=_run_transaction_context,
)

tool_find_shared_card_activity = InvestigationTool(
    metadata=ToolMetadata(
        name="find_shared_card_activity",
        description=(
            "Find other transactions that share the same card identifier as the target "
            "transaction. Useful for finding transactions plausibly linked by card reuse. "
            "card_key is an anonymized fingerprint, not a verified card number - this "
            "returns evidence, not a fraud verdict."
        ),
        source_query="find_shared_card_activity",
        primary_evidence_type=EvidenceType.SHARED_CARD,
        evidence_types=("shared_card",),
    ),
    input_schema=SharedCardInput,
    run=_run_shared_card,
)

tool_find_shared_device_activity = InvestigationTool(
    metadata=ToolMetadata(
        name="find_shared_device_activity",
        description=(
            "Find other transactions that share the same device identifier as the target "
            "transaction. Useful for finding transactions plausibly linked by device reuse. "
            "device_key ranges from highly specific to generic values - this returns "
            "evidence, not a fraud verdict."
        ),
        source_query="find_shared_device_activity",
        primary_evidence_type=EvidenceType.SHARED_DEVICE,
        evidence_types=("shared_device",),
    ),
    input_schema=SharedDeviceInput,
    run=_run_shared_device,
)

tool_find_shared_address_activity = InvestigationTool(
    metadata=ToolMetadata(
        name="find_shared_address_activity",
        description=(
            "Find other transactions that share the same billing address code as the "
            "target transaction. LOW-quality signal: addr1/addr2 are coarse regional/"
            "address codes in this dataset, not precise physical addresses, so this "
            "tends to return a high, often coincidental count. Returns evidence, not a "
            "fraud verdict."
        ),
        source_query="find_shared_address_activity",
        primary_evidence_type=EvidenceType.SHARED_ADDRESS,
        evidence_types=("shared_address",),
    ),
    input_schema=SharedAddressInput,
    run=_run_shared_address,
)

tool_find_shared_email_activity = InvestigationTool(
    metadata=ToolMetadata(
        name="find_shared_email_activity",
        description=(
            "Find other transactions that share the same purchaser or recipient email "
            "domain as the target transaction. LOW-quality signal: only the domain is "
            "ever disclosed (never a full address) and this dataset has very few distinct "
            "domains, so a shared common domain (e.g. gmail.com) is weak evidence of any "
            "real relationship. Returns evidence, not a fraud verdict - email domain "
            "linkage is not automatically fraudulent."
        ),
        source_query="find_shared_email_activity",
        primary_evidence_type=EvidenceType.SHARED_EMAIL_DOMAIN,
        evidence_types=("shared_email_domain",),
    ),
    input_schema=SharedEmailInput,
    run=_run_shared_email,
)

tool_investigate_transaction_network = InvestigationTool(
    metadata=ToolMetadata(
        name="investigate_transaction_network",
        description=(
            "Return the 2-hop fan-out from the target transaction, broken down by which "
            "linking entity type (card, address, email domain, device) reached each "
            "related transaction. Useful for seeing at a glance which linkage type "
            "dominates a transaction's network, without collapsing them into a single "
            "relationship count. Returns evidence, not a fraud verdict."
        ),
        source_query="investigate_transaction_network",
        primary_evidence_type=EvidenceType.NETWORK_PATTERN,
        evidence_types=("network_pattern",),
    ),
    input_schema=NetworkPatternInput,
    run=_run_network_pattern,
)


ALLOWED_INVESTIGATION_TOOLS: tuple[InvestigationTool, ...] = (
    tool_get_transaction_context,
    tool_find_shared_card_activity,
    tool_find_shared_device_activity,
    tool_find_shared_address_activity,
    tool_find_shared_email_activity,
    tool_investigate_transaction_network,
)
