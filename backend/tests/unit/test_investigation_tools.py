"""Offline tests for app/investigation/schemas.py and app/investigation/tools.py.

Input validation is tested directly against the Pydantic schemas (no
TigerGraph involved). Tool bodies are tested by monkeypatching
app.tigergraph.queries, exactly like tests/unit/test_evidence_aggregate.py
does for aggregate_evidence - these tests exist to confirm each tool
wires the right Phase 2A query into the right Phase 2B normalizer, not to
re-test query or normalization correctness (covered by
tests/tigergraph/test_investigation_queries.py and
tests/unit/test_evidence_normalize.py).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from app.evidence.models import EvidenceType, QueryStatus
from app.investigation import tools as t
from app.investigation.schemas import (
    NetworkPatternInput,
    SharedAddressInput,
    SharedCardInput,
    SharedDeviceInput,
    SharedEmailInput,
    TransactionContextInput,
)
from app.tigergraph.queries import (
    NetworkSummary,
    SharedEmailActivity,
    SharedEntityActivity,
    TransactionContext,
)

# ---------------------------------------------------------------- schemas


class TestTransactionIdValidation:
    def test_a_valid_id_round_trips_unchanged(self):
        assert TransactionContextInput(transaction_id="2987937").transaction_id == "2987937"

    def test_surrounding_whitespace_is_stripped(self):
        assert TransactionContextInput(transaction_id="  2987937  ").transaction_id == "2987937"

    def test_empty_string_is_rejected(self):
        with pytest.raises(ValidationError):
            TransactionContextInput(transaction_id="")

    def test_whitespace_only_string_is_rejected(self):
        with pytest.raises(ValidationError):
            TransactionContextInput(transaction_id="   ")

    def test_missing_field_is_rejected(self):
        with pytest.raises(ValidationError):
            TransactionContextInput()

    def test_none_is_rejected(self):
        with pytest.raises(ValidationError):
            TransactionContextInput(transaction_id=None)

    def test_an_int_is_rejected_not_silently_coerced(self):
        with pytest.raises(ValidationError):
            TransactionContextInput(transaction_id=2987937)

    def test_a_list_is_rejected(self):
        with pytest.raises(ValidationError):
            TransactionContextInput(transaction_id=["2987937"])

    def test_an_absurdly_long_id_is_rejected(self):
        with pytest.raises(ValidationError):
            TransactionContextInput(transaction_id="9" * 200)

    def test_unexpected_extra_fields_are_rejected(self):
        with pytest.raises(ValidationError):
            TransactionContextInput(transaction_id="2987937", extra_field="nope")

    def test_does_not_assume_a_numeric_only_id_format(self):
        # The dataset's real IDs happen to be numeric, but this layer
        # must not bake that assumption in - any non-empty, reasonably
        # short string is a valid transaction_id.
        assert TransactionContextInput(transaction_id="TXN-ABC-123").transaction_id == "TXN-ABC-123"

    @pytest.mark.parametrize(
        "cls",
        [
            TransactionContextInput,
            SharedCardInput,
            SharedDeviceInput,
            SharedAddressInput,
            SharedEmailInput,
            NetworkPatternInput,
        ],
    )
    def test_every_per_tool_input_schema_enforces_the_same_rule(self, cls):
        with pytest.raises(ValidationError):
            cls(transaction_id="")


# ---------------------------------------------------------------- tool wiring


class TestToolBodiesWireTheRightQueryToTheRightNormalizer:
    """Each _run_* function should do nothing but call one Phase 2A query
    and hand its result to one Phase 2B normalizer - verified by
    monkeypatching the query and checking the returned Evidence's type
    and key facts came straight through."""

    def test_transaction_context(self, monkeypatch):
        ctx = TransactionContext(
            transaction_id="T1", attributes={"is_fraud": True, "product_cd": "W"},
            card={"id": "card-1", "type": "Card"}, address=None,
            purchaser_email=None, recipient_email=None, device=None,
        )
        monkeypatch.setattr(t.queries, "get_transaction_context", lambda client, txn: ctx)
        ev = t._run_transaction_context(object(), TransactionContextInput(transaction_id="T1"))
        assert ev.evidence_type == EvidenceType.TRANSACTION_CONTEXT
        assert ev.metrics["has_card"] is True
        assert ev.metrics["is_fraud_label"] is True

    def test_shared_card(self, monkeypatch):
        result = SharedEntityActivity("Card", "T1", {"id": "card-1", "type": "Card"}, [])
        monkeypatch.setattr(t.queries, "find_shared_card_activity", lambda client, txn: result)
        ev = t._run_shared_card(object(), SharedCardInput(transaction_id="T1"))
        assert ev.evidence_type == EvidenceType.SHARED_CARD
        assert ev.status == QueryStatus.EMPTY  # linked card, but nothing else shares it

    def test_shared_device(self, monkeypatch):
        result = SharedEntityActivity("Device", "T1", None, [])
        monkeypatch.setattr(t.queries, "find_shared_device_activity", lambda client, txn: result)
        ev = t._run_shared_device(object(), SharedDeviceInput(transaction_id="T1"))
        assert ev.evidence_type == EvidenceType.SHARED_DEVICE
        assert ev.status == QueryStatus.EMPTY  # no device at all - a real fact, not an error

    def test_shared_address(self, monkeypatch):
        related = [
            {"transaction_id": f"T{i}", "is_fraud": False, "transaction_amt": 1.0,
             "transaction_dt": 1, "product_cd": "W"}
            for i in range(2, 5)
        ]
        result = SharedEntityActivity("Address", "T1", {"id": "addr-1", "type": "Address"}, related)
        monkeypatch.setattr(t.queries, "find_shared_address_activity", lambda client, txn: result)
        ev = t._run_shared_address(object(), SharedAddressInput(transaction_id="T1"))
        assert ev.evidence_type == EvidenceType.SHARED_ADDRESS
        assert ev.status == QueryStatus.SUCCESS
        assert ev.metrics["related_transaction_count"] == 3
        assert ev.quality.value == "LOW"  # the real, documented Address finding must survive

    def test_shared_email(self, monkeypatch):
        result = SharedEmailActivity("T1", {"id": "gmail.com", "type": "EmailDomain"}, [])
        monkeypatch.setattr(t.queries, "find_shared_email_activity", lambda client, txn: result)
        ev = t._run_shared_email(object(), SharedEmailInput(transaction_id="T1"))
        assert ev.evidence_type == EvidenceType.SHARED_EMAIL_DOMAIN
        assert ev.metrics["purchaser_domain"] == "gmail.com"

    def test_network_pattern(self, monkeypatch):
        summary = NetworkSummary(
            "T1",
            linked_entities={"Card": 1, "Address": 0, "EmailDomain_purchaser": 0,
                              "EmailDomain_recipient": 0, "Device": 0},
            related_by_entity_type={"Card": 5, "Address": 0, "EmailDomain_purchaser": 0,
                                     "EmailDomain_recipient": 0, "Device": 0},
        )
        monkeypatch.setattr(t.queries, "investigate_transaction_network", lambda client, txn: summary)
        ev = t._run_network_pattern(object(), NetworkPatternInput(transaction_id="T1"))
        assert ev.evidence_type == EvidenceType.NETWORK_PATTERN
        assert ev.metrics["related_by_entity_type"]["Card"] == 5
        # High-quality-only fan-out: not the LOW-quality Address/EmailDomain finding.
        assert ev.quality.value == "MEDIUM"


class TestAllowlistAndMetadata:
    def test_exactly_six_tools_are_allowed(self):
        assert len(t.ALLOWED_INVESTIGATION_TOOLS) == 6

    def test_allowed_tool_names_match_the_six_phase_2a_capabilities(self):
        names = {tool.name for tool in t.ALLOWED_INVESTIGATION_TOOLS}
        assert names == {
            "get_transaction_context",
            "find_shared_card_activity",
            "find_shared_device_activity",
            "find_shared_address_activity",
            "find_shared_email_activity",
            "investigate_transaction_network",
        }

    def test_no_raw_gsql_or_destructive_tool_is_defined_anywhere_in_this_module(self):
        forbidden = ("execute_gsql", "run_query", "run_raw_tigergraph", "drop_graph",
                     "clear_graph_data", "create_graph", "alter_schema", "load_data")
        names = {tool.name for tool in t.ALLOWED_INVESTIGATION_TOOLS}
        assert not (names & set(forbidden))
        # And not lurking as a module-level tool-shaped object under another name.
        for attr_name in dir(t):
            if attr_name.startswith("_"):
                continue
            value = getattr(t, attr_name)
            if isinstance(value, t.InvestigationTool):
                assert value.name in names

    @pytest.mark.parametrize("tool", t.ALLOWED_INVESTIGATION_TOOLS, ids=lambda tool: tool.name)
    def test_every_tool_has_complete_metadata(self, tool):
        d = tool.metadata.as_dict()
        assert d["name"] == tool.name
        assert d["description"]
        assert "detects fraud" not in d["description"].lower()
        assert "this detects" not in d["description"].lower()
        assert d["read_only"] is True
        assert d["requires_transaction_id"] is True
        assert d["evidence_types"]
        assert d["estimated_cost"] in ("LOW", "MEDIUM", "HIGH")
        assert issubclass(tool.input_schema, BaseModel)

    @pytest.mark.parametrize("tool", t.ALLOWED_INVESTIGATION_TOOLS, ids=lambda tool: tool.name)
    def test_every_tool_input_schema_requires_transaction_id(self, tool):
        assert "transaction_id" in tool.input_schema.model_fields
