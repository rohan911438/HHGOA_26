"""Phase 2K - shared fixtures for offline API tests.

Fakes exactly the two things that would otherwise require live
infrastructure/credentials: TigerGraph (by monkeypatching
`app.investigation.tools.queries`, the same convention
`tests/unit/test_agent_orchestrator.py` already established) and the LLM
(`FakeLLMClient`, injected via FastAPI's `dependency_overrides`).
Nothing about the API layer itself, `InvestigationService`,
`UncertaintyEngine`, `PolicyEngine`, or `CaseManager` is faked - every
offline API test still runs the real deterministic pipeline end to end
against the faked graph data.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agent import FakeLLMClient, LLMDecision, LLMDecisionAction
from app.api import create_app
from app.api.dependencies import get_llm_client, get_tigergraph_client
from app.investigation import tools as t
from app.tigergraph.client import ConnectionStatus
from app.tigergraph.queries import (
    NetworkSummary,
    SharedEmailActivity,
    SharedEntityActivity,
    TransactionContext,
)

KNOWN_TXN = "2987937"


class FakeSettings:
    tg_host = "https://example.com"
    tg_timeout_seconds = 60


class FakeTigerGraphClient:
    """Stands in for a real `TigerGraphClient` in offline tests. Its
    `.health()` is itself fake (always reports connected) - that is
    correct only because this is a test double the production code path
    never uses: `app/api/routes/health.py` calls the exact same method
    on the *real* `TigerGraphClient` (Phase 1, unmodified), which always
    performs a genuine probe - see its docstring."""

    settings = FakeSettings()

    def health(self) -> ConnectionStatus:
        return ConnectionStatus(connected=True, auth_method="fake", host="fake-host", latency_ms=1.0)


def _linked_entity(entity_type: str, entity_id: str, related: list[dict], txn: str) -> SharedEntityActivity:
    return SharedEntityActivity(entity_type, txn, {"id": entity_id, "type": entity_type}, related)


def _empty_entity(entity_type: str, txn: str) -> SharedEntityActivity:
    return SharedEntityActivity(entity_type, txn, None, [])


def _txn_row(i: int) -> dict:
    return {"transaction_id": f"T{i}", "is_fraud": False, "transaction_amt": 1.0, "transaction_dt": 1, "product_cd": "W"}


@pytest.fixture
def fake_graph(monkeypatch) -> str:
    """Every transaction id resolves to the same fixed evidence shape
    (two linked entity types: Card + Device) - a deterministic stand-in
    for one real graph transaction, matching
    tests/unit/test_agent_orchestrator.py's set_strong_two_types. Returns
    the conventional known transaction id these tests use."""

    def get_transaction_context(client, txn):
        return TransactionContext(
            transaction_id=txn,
            attributes={"is_fraud": False, "transaction_amt": 50.0, "product_cd": "W"},
            card={"id": "card-1", "type": "Card"},
            address=None,
            purchaser_email=None,
            recipient_email=None,
            device={"id": "dev-1", "type": "Device"},
        )

    def find_shared_card_activity(client, txn):
        return _linked_entity("Card", "card-1", [_txn_row(2)], txn)

    def find_shared_device_activity(client, txn):
        return _linked_entity("Device", "dev-1", [_txn_row(3)], txn)

    def find_shared_address_activity(client, txn):
        return _empty_entity("Address", txn)

    def find_shared_email_activity(client, txn):
        return SharedEmailActivity(txn, None)

    def investigate_transaction_network(client, txn):
        counts = {"Card": 1, "Address": 0, "EmailDomain_purchaser": 0, "EmailDomain_recipient": 0, "Device": 1}
        return NetworkSummary(txn, counts, counts)

    monkeypatch.setattr(t.queries, "get_transaction_context", get_transaction_context)
    monkeypatch.setattr(t.queries, "find_shared_card_activity", find_shared_card_activity)
    monkeypatch.setattr(t.queries, "find_shared_device_activity", find_shared_device_activity)
    monkeypatch.setattr(t.queries, "find_shared_address_activity", find_shared_address_activity)
    monkeypatch.setattr(t.queries, "find_shared_email_activity", find_shared_email_activity)
    monkeypatch.setattr(t.queries, "investigate_transaction_network", investigate_transaction_network)
    return KNOWN_TXN


@pytest.fixture
def app(fake_graph):
    application = create_app()
    application.dependency_overrides[get_tigergraph_client] = lambda: FakeTigerGraphClient()
    application.dependency_overrides[get_llm_client] = lambda: FakeLLMClient(
        decisions=[LLMDecision(action=LLMDecisionAction.CONTINUE, reason="deterministic fake: sufficient")],
        explanation="SYNTHETIC TEST EXPLANATION for the Phase 2K API test suite.",
    )
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def set_llm(app, llm) -> None:
    """Swap the LLM dependency override for a single test (a failing
    agent, an exhausted-decision one, etc.) without rebuilding the app."""
    app.dependency_overrides[get_llm_client] = lambda: llm
