"""Offline tests for app/investigation/registry.py.

Every TigerGraph call is monkeypatched on app.investigation.tools.queries
(the module tools.py imports as `queries`) - no live connection involved.
Covers registry mechanics, input validation end-to-end through
`execute()`, SUCCESS/EMPTY/ERROR status propagation, error
classification, the security allowlist, and determinism - the Phase 2C
success criteria list.
"""

from __future__ import annotations

import time

import pytest

from app.evidence.models import QueryStatus
from app.investigation import tools as t
from app.investigation.errors import ToolErrorType
from app.investigation.registry import ToolNotRegisteredError, ToolRegistry, build_default_registry
from app.tigergraph.client import TigerGraphQueryError, TigerGraphUnavailable
from app.tigergraph.queries import SharedEntityActivity


class FakeSettings:
    # classify_network (app.tigergraph.diagnostics) does a real DNS check
    # before pattern-matching the error text, so this must be a host that
    # actually resolves - same convention tests/unit/test_tigergraph_diagnostics.py
    # already uses, for the same reason.
    tg_host = "https://example.com"
    tg_timeout_seconds = 60


class FakeClient:
    settings = FakeSettings()


@pytest.fixture
def registry() -> ToolRegistry:
    return build_default_registry()


# ---------------------------------------------------------------- registry mechanics


class TestRegistryMechanics:
    def test_register_then_get_returns_the_same_tool(self):
        registry = ToolRegistry()
        dummy = t.tool_get_transaction_context
        registry.register(dummy)
        assert registry.get("get_transaction_context") is dummy

    def test_list_tools_returns_metadata_for_every_registered_tool(self, registry):
        listed = registry.list_tools()
        assert {entry["name"] for entry in listed} == {tool.name for tool in t.ALLOWED_INVESTIGATION_TOOLS}

    def test_list_tools_is_sorted_deterministically(self, registry):
        names = [entry["name"] for entry in registry.list_tools()]
        assert names == sorted(names)

    def test_unknown_tool_name_raises_a_clear_error(self, registry):
        with pytest.raises(ToolNotRegisteredError):
            registry.get("run_gsql")

    def test_execute_on_an_unknown_tool_also_raises_rather_than_returning_a_result(self, registry):
        with pytest.raises(ToolNotRegisteredError):
            registry.execute("execute_gsql", {"transaction_id": "T1"}, FakeClient())

    def test_default_registry_has_exactly_the_six_allowed_tools(self, registry):
        assert len(registry.list_tools()) == 6


# ---------------------------------------------------------------- input validation via execute()


class TestInputValidationThroughExecute:
    def test_valid_transaction_id_is_accepted(self, monkeypatch, registry):
        result = SharedEntityActivity("Card", "T1", None, [])
        monkeypatch.setattr(t.queries, "find_shared_card_activity", lambda client, txn: result)
        res = registry.execute("find_shared_card_activity", {"transaction_id": "T1"}, FakeClient())
        assert res.status == QueryStatus.EMPTY
        assert res.error is None

    def test_empty_transaction_id_becomes_a_validation_error_not_an_exception(self, registry):
        res = registry.execute("find_shared_card_activity", {"transaction_id": ""}, FakeClient())
        assert res.status == QueryStatus.ERROR
        assert res.error.error_type == ToolErrorType.VALIDATION_ERROR

    def test_wrong_type_becomes_a_validation_error(self, registry):
        res = registry.execute("find_shared_card_activity", {"transaction_id": 12345}, FakeClient())
        assert res.status == QueryStatus.ERROR
        assert res.error.error_type == ToolErrorType.VALIDATION_ERROR

    def test_missing_field_becomes_a_validation_error(self, registry):
        res = registry.execute("find_shared_card_activity", {}, FakeClient())
        assert res.status == QueryStatus.ERROR
        assert res.error.error_type == ToolErrorType.VALIDATION_ERROR

    def test_validation_error_never_reaches_tigergraph(self, monkeypatch, registry):
        called = []
        monkeypatch.setattr(
            t.queries, "find_shared_card_activity", lambda client, txn: called.append(txn)
        )
        registry.execute("find_shared_card_activity", {"transaction_id": ""}, FakeClient())
        assert called == []


# ---------------------------------------------------------------- status propagation


class TestStatusPropagation:
    def test_success_status_propagates(self, monkeypatch, registry):
        related = [{"transaction_id": "T2", "is_fraud": False, "transaction_amt": 1.0,
                     "transaction_dt": 1, "product_cd": "W"}]
        result = SharedEntityActivity("Card", "T1", {"id": "card-1", "type": "Card"}, related)
        monkeypatch.setattr(t.queries, "find_shared_card_activity", lambda client, txn: result)
        res = registry.execute("find_shared_card_activity", {"transaction_id": "T1"}, FakeClient())
        assert res.status == QueryStatus.SUCCESS
        assert res.evidence.status == QueryStatus.SUCCESS
        assert res.error is None

    def test_empty_status_propagates_and_is_not_an_error(self, monkeypatch, registry):
        result = SharedEntityActivity("Device", "T1", None, [])
        monkeypatch.setattr(t.queries, "find_shared_device_activity", lambda client, txn: result)
        res = registry.execute("find_shared_device_activity", {"transaction_id": "T1"}, FakeClient())
        assert res.status == QueryStatus.EMPTY
        assert res.error is None  # critical: EMPTY must never carry an error

    def test_error_status_propagates_when_the_query_raises(self, monkeypatch, registry):
        def boom(client, txn):
            raise TigerGraphUnavailable("Could not mint a REST++ token from TG_SECRET: boom")

        monkeypatch.setattr(t.queries, "find_shared_card_activity", boom)
        res = registry.execute("find_shared_card_activity", {"transaction_id": "T1"}, FakeClient())
        assert res.status == QueryStatus.ERROR
        assert res.evidence.status == QueryStatus.ERROR
        assert res.error is not None


# ---------------------------------------------------------------- error normalization


class TestErrorNormalization:
    def test_tigergraph_unavailable_server_error_is_classified(self, monkeypatch, registry):
        def boom(client, txn):
            raise TigerGraphUnavailable(
                "Could not mint a REST++ token from TG_SECRET: HTTPError: 500 Server Error"
            )

        monkeypatch.setattr(t.queries, "get_transaction_context", boom)
        res = registry.execute("get_transaction_context", {"transaction_id": "T1"}, FakeClient())
        assert res.error.error_type == ToolErrorType.TIGERGRAPH_UNAVAILABLE

    def test_authentication_failure_is_classified_distinctly(self, monkeypatch, registry):
        def boom(client, txn):
            raise TigerGraphUnavailable("401 Unauthorized: invalid secret")

        monkeypatch.setattr(t.queries, "get_transaction_context", boom)
        res = registry.execute("get_transaction_context", {"transaction_id": "T1"}, FakeClient())
        assert res.error.error_type == ToolErrorType.AUTHENTICATION_ERROR

    def test_nonexistent_transaction_is_not_found_not_a_generic_query_error(self, monkeypatch, registry):
        def boom(client, txn):
            raise TigerGraphQueryError(f"transaction '{txn}' does not exist")

        monkeypatch.setattr(t.queries, "get_transaction_context", boom)
        res = registry.execute("get_transaction_context", {"transaction_id": "ghost"}, FakeClient())
        assert res.error.error_type == ToolErrorType.NOT_FOUND

    def test_a_different_query_failure_is_a_generic_query_error(self, monkeypatch, registry):
        def boom(client, txn):
            raise TigerGraphQueryError("get_transaction_context failed: something else broke")

        monkeypatch.setattr(t.queries, "get_transaction_context", boom)
        res = registry.execute("get_transaction_context", {"transaction_id": "T1"}, FakeClient())
        assert res.error.error_type == ToolErrorType.QUERY_ERROR

    def test_an_unexpected_exception_becomes_internal_error_not_a_crash(self, monkeypatch, registry):
        def boom(client, txn):
            raise ValueError("something this project never anticipated")

        monkeypatch.setattr(t.queries, "get_transaction_context", boom)
        res = registry.execute("get_transaction_context", {"transaction_id": "T1"}, FakeClient())
        assert res.status == QueryStatus.ERROR
        assert res.error.error_type == ToolErrorType.INTERNAL_ERROR

    def test_timeout_becomes_a_structured_error_not_a_hang(self, monkeypatch, registry):
        def slow(client, txn):
            time.sleep(1.5)
            return SharedEntityActivity("Card", txn, None, [])

        monkeypatch.setattr(t.queries, "find_shared_card_activity", slow)
        started = time.perf_counter()
        res = registry.execute(
            "find_shared_card_activity", {"transaction_id": "T1"}, FakeClient(), timeout_seconds=0.1
        )
        wall = time.perf_counter() - started
        assert res.status == QueryStatus.ERROR
        assert res.error.error_type == ToolErrorType.TIMEOUT
        # The whole point of the timeout: execute() must return long before
        # the slow call itself would have finished (1.5s).
        assert wall < 1.0

    def test_no_credential_shaped_text_appears_in_a_normalized_error(self, monkeypatch, registry):
        def boom(client, txn):
            raise TigerGraphUnavailable(
                "Could not mint a REST++ token from TG_SECRET: 401 Unauthorized"
            )

        monkeypatch.setattr(t.queries, "get_transaction_context", boom)
        res = registry.execute("get_transaction_context", {"transaction_id": "T1"}, FakeClient())
        # The wrapper message never embeds the secret's actual value (only
        # ever the setting name TG_SECRET) - confirm that stays true here.
        assert "TG_SECRET" not in res.error.message or "=" not in res.error.message


# ---------------------------------------------------------------- security


class TestSecurityBoundary:
    def test_no_raw_gsql_tool_is_registered(self, registry):
        for forbidden in ("execute_gsql", "run_query", "run_raw_tigergraph", "gsql"):
            with pytest.raises(ToolNotRegisteredError):
                registry.get(forbidden)

    def test_no_destructive_tool_is_registered(self, registry):
        for forbidden in ("drop_graph", "clear_graph_data", "create_graph", "alter_schema", "load_data"):
            with pytest.raises(ToolNotRegisteredError):
                registry.get(forbidden)

    def test_mcp_destructive_blocklist_is_disjoint_from_the_registry(self, registry):
        from app.mcp.config import ALWAYS_BLOCKED

        registered_names = {entry["name"] for entry in registry.list_tools()}
        assert registered_names.isdisjoint(ALWAYS_BLOCKED)

    def test_all_registered_tools_are_read_only(self, registry):
        assert all(entry["read_only"] is True for entry in registry.list_tools())


# ---------------------------------------------------------------- determinism


class TestDeterminism:
    def test_identical_mocked_response_produces_an_identical_result(self, monkeypatch, registry):
        related = [{"transaction_id": "T2", "is_fraud": False, "transaction_amt": 1.0,
                     "transaction_dt": 1, "product_cd": "W"}]
        result = SharedEntityActivity("Card", "T1", {"id": "card-1", "type": "Card"}, related)
        monkeypatch.setattr(t.queries, "find_shared_card_activity", lambda client, txn: result)

        res_a = registry.execute("find_shared_card_activity", {"transaction_id": "T1"}, FakeClient())
        res_b = registry.execute("find_shared_card_activity", {"transaction_id": "T1"}, FakeClient())

        assert res_a.status == res_b.status
        assert res_a.evidence.evidence_id == res_b.evidence.evidence_id
        assert res_a.evidence.metrics == res_b.evidence.metrics
        assert res_a.summary == res_b.summary
