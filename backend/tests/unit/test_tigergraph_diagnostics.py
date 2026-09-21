"""Offline tests for TigerGraph connection diagnostics.

No network, no credentials. Covers the CONFIGURATION-missing path exactly
as a fresh clone of this repo will hit it, and the classification bug that
was caught by hand while building test_tigergraph.py: a wrapped
TigerGraphUnavailable whose own message contains the word "token" must not
be misclassified as an authentication failure when the actual cause was a
DNS lookup failure.
"""

from __future__ import annotations

import socket

import pytest

from app.config import Settings
from app.tigergraph.client import TigerGraphUnavailable
from app.tigergraph.diagnostics import all_passed, classify_network, root_cause, run_checks


def make_settings(**overrides) -> Settings:
    base = dict(
        tg_host="",
        tg_graphname="",
        tg_username="",
        tg_password="",
        tg_secret="",
        tg_api_token="",
        tg_jwt_token="",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


class TestConfigurationGate:
    def test_all_placeholders_reports_configuration_failures(self):
        s = make_settings(
            tg_host="https://YOUR_TIGERGRAPH_HOST",
            tg_graphname="YOUR_GRAPH_NAME",
            tg_password="YOUR_TIGERGRAPH_PASSWORD",
        )
        results = run_checks(s)
        by_name = {r.name: r for r in results}

        assert by_name["Host configured"].ok is False
        assert by_name["Host configured"].category == "CONFIGURATION"
        assert by_name["Graph name configured"].ok is False
        assert by_name["Credential configured"].ok is False

        # Every downstream check is skipped, not silently marked passing.
        assert by_name["Host reachable"].ok is None
        assert by_name["Read-only query successful"].ok is None
        assert all_passed(results) is False

    def test_empty_settings_reports_configuration_failures(self):
        s = make_settings()
        results = run_checks(s)
        assert all_passed(results) is False
        assert any(r.category == "CONFIGURATION" for r in results)

    def test_partial_configuration_only_flags_whats_missing(self):
        s = make_settings(tg_host="https://real.i234.tgcloud.io", tg_graphname="")
        results = run_checks(s)
        by_name = {r.name: r for r in results}
        assert by_name["Host configured"].ok is True
        assert by_name["Graph name configured"].ok is False


class TestRootCause:
    def test_walks_cause_chain_to_the_original_exception(self):
        original = socket.gaierror("getaddrinfo failed")
        try:
            try:
                raise original
            except socket.gaierror as inner:
                raise ConnectionError("could not connect") from inner
        except ConnectionError as wrapped:
            found = root_cause(wrapped)
        assert found is original

    def test_returns_the_exception_itself_when_there_is_no_cause(self):
        exc = ValueError("standalone")
        assert root_cause(exc) is exc

    def test_does_not_loop_forever_on_a_self_referential_chain(self):
        exc = ValueError("self-referential")
        exc.__cause__ = exc
        # Must terminate, not hang.
        result = root_cause(exc)
        assert result is exc


class TestClassifyNetwork:
    """The bug this locks in: our own wrapper text says "...token from
    TG_SECRET..." on every secret-auth failure, so classifying by keyword
    match against that wrapper text turns *any* underlying failure -
    including a plain DNS miss - into a false AUTHENTICATION verdict.
    Classification must read the root cause instead.
    """

    def test_dns_failure_wrapped_in_our_token_message_is_NETWORK_not_AUTHENTICATION(self):
        dns_error = socket.gaierror("[Errno 11001] getaddrinfo failed")
        wrapped = TigerGraphUnavailable(
            "Could not mint a REST++ token from TG_SECRET: gaierror: getaddrinfo failed"
        )
        wrapped.__cause__ = dns_error

        category, detail = classify_network("https://this-host-does-not-exist.invalid", wrapped)

        assert category == "NETWORK"
        assert "DNS" in detail

    def test_connection_refused_wrapped_in_token_message_is_NETWORK(self):
        refused = ConnectionRefusedError("[Errno 111] Connection refused")
        wrapped = TigerGraphUnavailable(
            "Could not mint a REST++ token from TG_SECRET: ConnectionRefusedError: refused"
        )
        wrapped.__cause__ = refused

        # example.com resolves, so this exercises the "refused" keyword path
        # rather than the DNS path.
        category, _ = classify_network("https://example.com", wrapped)
        assert category == "NETWORK"

    def test_actual_401_is_classified_AUTHENTICATION(self):
        http_error = Exception("401 Client Error: Unauthorized for url: https://x/restpp/echo")
        wrapped = TigerGraphUnavailable(
            "Could not mint a REST++ token from TG_SECRET: Exception: 401 Client Error: Unauthorized"
        )
        wrapped.__cause__ = http_error

        category, _ = classify_network("https://example.com", wrapped)
        assert category == "AUTHENTICATION"

    def test_pytigergraph_user_authentication_failed_is_classified_AUTHENTICATION(self):
        """Regression test for a real bug caught against a live Savanna instance.

        pyTigerGraph's own TigerGraphException carries the message
        "User authentication failed" - no "401", "403" or "unauthoriz"
        substring at all. The first cut of the keyword list missed this
        entirely, so a wrong credential was silently classified as the
        non-fatal ENDPOINT bucket and the overall result came back
        `healthy: true` while the detail line plainly said authentication
        had failed. Caught by actually running the script against a real
        (wrong-credential-type) connection attempt, not by inspection.
        """
        tg_exception = Exception(("User authentication failed", None))
        wrapped = TigerGraphUnavailable(
            "Could not mint a REST++ token from TG_SECRET: TigerGraphException: "
            "('User authentication failed', None)"
        )
        wrapped.__cause__ = tg_exception

        # A real, resolvable host - a fictitious tgcloud.io subdomain would
        # fail DNS resolution and be classified NETWORK before the auth
        # keywords are ever checked, which isn't the scenario under test.
        category, _ = classify_network("https://example.com", wrapped)
        assert category == "AUTHENTICATION"

    def test_unrecognised_failure_on_a_live_host_is_ENDPOINT_not_a_false_pass(self):
        odd = Exception("something unexpected")
        wrapped = TigerGraphUnavailable(f"echo failed: {odd}")
        wrapped.__cause__ = odd

        category, _ = classify_network("https://example.com", wrapped)
        # Must not silently claim NETWORK or AUTHENTICATION when neither
        # signal is present - ENDPOINT is the honest "reached something,
        # unsure what" bucket.
        assert category == "ENDPOINT"


class _StubConnection:
    def __init__(self, gsql_result: str):
        self._gsql_result = gsql_result

    def gsql(self, _statement: str) -> str:
        return self._gsql_result


class _StubClient:
    """Minimal stand-in for TigerGraphClient - just enough surface for
    run_checks to reach the "Graph listing" step and stop there."""

    def __init__(self, settings: Settings, gsql_result: str):
        self.settings = settings
        self.connection = _StubConnection(gsql_result)

    def echo(self) -> str:
        return "hello GSQL"

    def get_schema(self):
        # Stop the checklist here - this test only cares about how the
        # "Graph listing" line is scored, not what comes after it.
        raise TigerGraphUnavailable("stub stops here")


class TestGraphListingHonesty:
    """Regression test for a real bug caught against a live Savanna
    instance: GSQL reports some failures (no graph exists yet) as plain
    text in what pyTigerGraph treats as a successful call, rather than
    raising. The first cut of run_checks put a checkmark next to a
    message that literally said "Semantic Check Fails".
    """

    def _settings(self) -> Settings:
        # Fake, structurally-shaped value only - never a real credential.
        return Settings(
            _env_file=None,
            tg_host="https://tg-example.i234.tgcloud.io",
            tg_graphname="HHGOA_FRAUD",
            tg_secret="fake00000000000000000000000secret",
        )

    def test_semantic_check_fails_is_not_marked_ok(self):
        client = _StubClient(
            self._settings(),
            "Semantic Check Fails: No graph available. Please create a graph first.",
        )
        results = run_checks(client.settings, client)
        listing = next(r for r in results if r.name == "Graph listing")
        assert listing.ok is None, "a failure message must not be marked ok=True"

    def test_a_real_graph_list_is_marked_ok(self):
        client = _StubClient(self._settings(), "Graphs:\n  - HHGOA_FRAUD")
        results = run_checks(client.settings, client)
        listing = next(r for r in results if r.name == "Graph listing")
        assert listing.ok is True
