"""Live tests for Phase 2A investigation queries.

Runs against the real, loaded HHGOA_FRAUD dev graph - requires a working
TigerGraph connection (backend/.env configured). Marked `tigergraph` so
the offline suite (`pytest -m "not tigergraph"` or the default CI run)
skips these; run explicitly with `pytest -m tigergraph` or
`make test-tg`.

Fixture transaction `2987937` and its exact neighborhood counts were
captured directly from the live dev graph while building
app/tigergraph/queries.py (see that module's docstring for the narrative:
this is the transaction whose shared-Address fan-out first surfaced the
"Address is a noisy signal" finding). If the dev subset is regenerated
with a different seed, these exact numbers will need updating - that's
expected and is exactly why they're asserted here: to notice when they
silently change.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.tigergraph.client import TigerGraphQueryError, get_client
from app.tigergraph import queries as q

pytestmark = pytest.mark.tigergraph

# A transaction confirmed present in the loaded dev subset with a real,
# multi-entity neighborhood (card, address, purchaser email; no device).
KNOWN_TXN = "2987937"
KNOWN_CARD_KEY = "18227|583.0|150.0|226.0"
KNOWN_ADDRESS_KEY = "299.0|87.0"
KNOWN_PURCHASER_DOMAIN = "sbcglobal.net"


@pytest.fixture(scope="module")
def client():
    settings = get_settings()
    if not settings.tg_configured:
        pytest.skip("TigerGraph not configured - set TG_HOST/TG_GRAPHNAME/TG_SECRET in .env")
    return get_client(settings)


class TestTransactionContext:
    def test_returns_the_transactions_own_attributes(self, client):
        ctx = q.get_transaction_context(client, KNOWN_TXN)
        assert ctx.transaction_id == KNOWN_TXN
        assert "transaction_amt" in ctx.attributes
        assert "is_fraud" in ctx.attributes

    def test_finds_the_directly_linked_card_and_address(self, client):
        ctx = q.get_transaction_context(client, KNOWN_TXN)
        assert ctx.card is not None and ctx.card["id"] == KNOWN_CARD_KEY
        assert ctx.address is not None and ctx.address["id"] == KNOWN_ADDRESS_KEY

    def test_a_transaction_with_no_identity_row_has_no_device(self, client):
        # KNOWN_TXN has no matching train_identity.csv row in the dev
        # subset, so it must have no Device - not an error, a real fact.
        ctx = q.get_transaction_context(client, KNOWN_TXN)
        assert ctx.device is None

    def test_nonexistent_transaction_raises_a_clear_error(self, client):
        with pytest.raises(TigerGraphQueryError):
            q.get_transaction_context(client, "does-not-exist-999999999")


class TestSharedCardActivity:
    def test_shared_card_returns_the_known_related_count(self, client):
        result = q.find_shared_card_activity(client, KNOWN_TXN)
        assert result.entity_type == "Card"
        assert result.entity["id"] == KNOWN_CARD_KEY
        assert result.related_count == 12

    def test_related_transactions_never_include_the_seed_itself(self, client):
        result = q.find_shared_card_activity(client, KNOWN_TXN)
        ids = {t["transaction_id"] for t in result.related_transactions}
        assert KNOWN_TXN not in ids

    def test_related_transactions_carry_the_summary_fields(self, client):
        result = q.find_shared_card_activity(client, KNOWN_TXN)
        assert result.related_transactions, "expected at least one related transaction"
        sample = result.related_transactions[0]
        assert set(sample.keys()) == {
            "transaction_id",
            "is_fraud",
            "transaction_amt",
            "transaction_dt",
            "product_cd",
        }


class TestSharedAddressActivity:
    def test_shared_address_is_the_noisier_signal(self, client):
        """Locks in the real finding documented in queries.py: this
        dataset's Address linkage fans out far more than Card - 312 vs
        12 for this same seed transaction. A future evidence model must
        not treat these two signal types as equally strong."""
        result = q.find_shared_address_activity(client, KNOWN_TXN)
        assert result.entity["id"] == KNOWN_ADDRESS_KEY
        assert result.related_count == 312


class TestSharedDeviceActivity:
    def test_transaction_with_no_device_returns_empty_not_an_error(self, client):
        result = q.find_shared_device_activity(client, KNOWN_TXN)
        assert result.entity is None
        assert result.related_count == 0


class TestSharedEmailActivity:
    def test_purchaser_domain_found_with_known_related_count(self, client):
        result = q.find_shared_email_activity(client, KNOWN_TXN)
        assert result.purchaser_domain["id"] == KNOWN_PURCHASER_DOMAIN
        assert len(result.via_purchaser_domain) == 20

    def test_no_recipient_domain_for_this_transaction(self, client):
        result = q.find_shared_email_activity(client, KNOWN_TXN)
        assert result.recipient_domain is None
        assert result.via_recipient_domain == []


class TestNetworkSummary:
    def test_breakdown_matches_the_individual_queries_exactly(self, client):
        """The whole point of investigate_transaction_network is that its
        per-entity-type breakdown must agree with what the dedicated
        shared-X queries independently return - if these ever disagree,
        one of the two query implementations has a real bug."""
        summary = q.investigate_transaction_network(client, KNOWN_TXN)
        card = q.find_shared_card_activity(client, KNOWN_TXN)
        addr = q.find_shared_address_activity(client, KNOWN_TXN)
        email = q.find_shared_email_activity(client, KNOWN_TXN)
        device = q.find_shared_device_activity(client, KNOWN_TXN)

        assert summary.related_by_entity_type["Card"] == card.related_count
        assert summary.related_by_entity_type["Address"] == addr.related_count
        assert summary.related_by_entity_type["EmailDomain_purchaser"] == len(email.via_purchaser_domain)
        assert summary.related_by_entity_type["EmailDomain_recipient"] == len(email.via_recipient_domain)
        assert summary.related_by_entity_type["Device"] == device.related_count

    def test_linked_entities_count_direct_neighbors_not_related_transactions(self, client):
        summary = q.investigate_transaction_network(client, KNOWN_TXN)
        # Exactly one Card, one Address, one purchaser EmailDomain - a
        # transaction has at most one of each by construction.
        assert summary.linked_entities["Card"] == 1
        assert summary.linked_entities["Address"] == 1
        assert summary.linked_entities["EmailDomain_purchaser"] == 1
        assert summary.linked_entities["Device"] == 0
