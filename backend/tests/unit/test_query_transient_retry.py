"""Offline tests for the transport-level retry in app/tigergraph/queries.py `_run`.

A fake connection stands in for pyTigerGraph so no TigerGraph instance
is needed. Covers: a transient DNS/connection failure is retried and
recovers, persistent transient failures still surface as
TigerGraphQueryError, and non-transport errors are never retried.
"""

from __future__ import annotations

import pytest
import requests

from app.tigergraph import queries as q
from app.tigergraph.client import TigerGraphQueryError


class _FakeConnection:
    def __init__(self, outcomes: list):
        self._outcomes = list(outcomes)
        self.calls = 0

    def runInterpretedQuery(self, gsql, params=None):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _FakeClient:
    def __init__(self, connection: _FakeConnection):
        self.connection = connection


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(q, "_TRANSIENT_BACKOFF_SECONDS", 0)


def _dns_failure() -> requests.exceptions.ConnectionError:
    return requests.exceptions.ConnectionError("Failed to resolve host ([Errno 11001] getaddrinfo failed)")


def test_transient_connection_error_is_retried_and_recovers():
    conn = _FakeConnection([_dns_failure(), [{"Seed": []}]])
    assert q._run(_FakeClient(conn), "get_transaction_context", "Q", "2987937") == [{"Seed": []}]
    assert conn.calls == 2


def test_builtin_connection_reset_is_also_retried():
    conn = _FakeConnection([ConnectionResetError(10054, "reset"), [{"ok": 1}]])
    assert q._run(_FakeClient(conn), "find_shared_card_activity", "Q", "2987937") == [{"ok": 1}]
    assert conn.calls == 2


def test_persistent_transient_failure_raises_after_all_attempts():
    conn = _FakeConnection([_dns_failure() for _ in range(q._TRANSIENT_ATTEMPTS)])
    with pytest.raises(TigerGraphQueryError, match="get_transaction_context failed: ConnectionError"):
        q._run(_FakeClient(conn), "get_transaction_context", "Q", "2987937")
    assert conn.calls == q._TRANSIENT_ATTEMPTS


def test_non_transport_errors_are_not_retried():
    conn = _FakeConnection([ValueError("GSQL semantic error"), [{"never": "reached"}]])
    with pytest.raises(TigerGraphQueryError, match="ValueError: GSQL semantic error"):
        q._run(_FakeClient(conn), "get_transaction_context", "Q", "2987937")
    assert conn.calls == 1


def test_read_timeouts_are_not_retried():
    conn = _FakeConnection([requests.exceptions.ReadTimeout("read timed out"), [{"never": "reached"}]])
    with pytest.raises(TigerGraphQueryError, match="ReadTimeout"):
        q._run(_FakeClient(conn), "investigate_transaction_network", "Q", "2987937")
    assert conn.calls == 1
