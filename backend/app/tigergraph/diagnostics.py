"""TigerGraph connection diagnostics: classification and the check pipeline.

Shared by `scripts/test_tigergraph.py` (developer CLI) and, later, the
`/health/tigergraph` API endpoint - one place decides what "the connection
is broken because X" means, so the CLI and the API never disagree.

Every failure is classified into one of:

    CONFIGURATION   required setting missing, before any network call
    AUTHENTICATION  reached the server, credential rejected
    NETWORK         DNS / connection / TLS failure, could not reach host
    ENDPOINT        reached a host but it did not behave like TigerGraph
    GRAPH           connected, but the named graph does not exist
    SDK             pyTigerGraph raised something unexpected
    SERVER          TigerGraph reported a server-side error

Classification reads the *root cause* of a chained exception, not the text
of our own wrapper messages. TigerGraphClient wraps driver errors in
messages like "Could not mint a REST++ token from TG_SECRET: ..." - that
wrapper text itself contains the word "token", so keyword-matching against
it would misclassify a plain DNS failure as an authentication failure. The
real signal (ConnectionError, HTTPError, gaierror, a 401 response, ...)
lives on `__cause__`, which this module walks to instead.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from app.config import Settings, get_settings
from app.tigergraph.client import (
    TigerGraphClient,
    TigerGraphQueryError,
    TigerGraphUnavailable,
    get_client,
)

CATEGORIES = (
    "CONFIGURATION",
    "AUTHENTICATION",
    "NETWORK",
    "ENDPOINT",
    "GRAPH",
    "SDK",
    "SERVER",
)


@dataclass
class CheckResult:
    name: str
    ok: bool | None  # True, False, or None for "skipped"
    detail: str = ""
    category: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "status": "ok" if self.ok else ("skipped" if self.ok is None else "failed"),
            "category": self.category or None,
            "detail": self.detail,
        }


def root_cause(exc: BaseException, max_depth: int = 6) -> BaseException:
    """Walk `__cause__` / `__context__` to the original exception."""
    seen = exc
    for _ in range(max_depth):
        nxt = seen.__cause__ or seen.__context__
        if nxt is None or nxt is seen:
            break
        seen = nxt
    return seen


def resolves(hostname: str) -> bool:
    try:
        socket.getaddrinfo(hostname, None)
        return True
    except socket.gaierror:
        return False


def classify_network(host: str, exc: Exception) -> tuple[str, str]:
    """Classify a connection failure from its root cause."""
    cause = root_cause(exc)
    wrapper_msg = f"{type(exc).__name__}: {exc}"
    cause_msg = f"{type(cause).__name__}: {cause}"
    lowered = cause_msg.lower()

    parsed = urlparse(host if "://" in host else f"https://{host}")
    hostname = parsed.hostname or host

    if not resolves(hostname) or "getaddrinfo" in lowered or "name or service not known" in lowered:
        return "NETWORK", f"DNS resolution failed for '{hostname}': {wrapper_msg}"
    # Checked before the generic "timeout" match below: "504 Gateway
    # Timeout" contains the word "timeout" too, but it's an HTTP status
    # from a server that responded - a SERVER-side signal, not evidence
    # we never reached anything (caught by a test using this exact code).
    if any(
        k in lowered
        for k in ("500", "502", "503", "504", "internal server error", "bad gateway",
                   "service unavailable", "gateway timeout")
    ):
        return "SERVER", f"TigerGraph reported a server-side error: {wrapper_msg}"
    if "timed out" in lowered or "timeout" in lowered:
        return "NETWORK", f"connection to '{hostname}' timed out: {wrapper_msg}"
    if "certificate" in lowered or "ssl" in lowered:
        return "NETWORK", f"TLS handshake failed for '{hostname}': {wrapper_msg}"
    if "refused" in lowered or "econnrefused" in lowered:
        return "NETWORK", f"connection refused by '{hostname}': {wrapper_msg}"
    if any(
        k in lowered
        for k in (
            "401",
            "403",
            "unauthoriz",
            "forbidden",
            "invalid secret",
            "invalid token",
            "invalid password",
            "authenticat",  # matches "authentication failed", "user authentication failed", etc.
            "access denied",
            "permission denied",
        )
    ):
        return "AUTHENTICATION", wrapper_msg
    if any(k in lowered for k in ("404", "not found")):
        return "ENDPOINT", f"'{hostname}' did not behave like a TigerGraph server: {wrapper_msg}"
    return "ENDPOINT", wrapper_msg


def run_checks(settings: Settings | None = None, client: TigerGraphClient | None = None) -> list[CheckResult]:
    """Run the full connection checklist. Never raises."""
    s = settings or get_settings()
    results: list[CheckResult] = []

    results.append(
        CheckResult("Host configured", s.tg_host_set, s.tg_host if s.tg_host_set else "TG_HOST is not set",
                     "" if s.tg_host_set else "CONFIGURATION")
    )
    results.append(
        CheckResult("Graph name configured", s.tg_graph_set,
                     s.tg_graphname if s.tg_graph_set else "TG_GRAPHNAME is not set",
                     "" if s.tg_graph_set else "CONFIGURATION")
    )
    cred_ok = s.tg_auth_method != "none"
    results.append(
        CheckResult(
            "Credential configured",
            cred_ok,
            f"using {s.tg_auth_method}" if cred_ok
            else "none of TG_API_TOKEN / TG_JWT_TOKEN / TG_SECRET / TG_PASSWORD is set",
            "" if cred_ok else "CONFIGURATION",
        )
    )

    config_ok = s.tg_host_set and s.tg_graph_set and cred_ok
    if not config_ok:
        for name in (
            "Host reachable",
            "Authentication successful",
            "Graph accessible",
            "Schema accessible",
            "Read-only query successful",
        ):
            results.append(CheckResult(name, None, "skipped - fix configuration first"))
        return results

    c = client or get_client(s)
    import time

    started = time.perf_counter()
    try:
        c.echo()
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        results.append(CheckResult("Host reachable", True, f"{elapsed} ms"))
        results.append(CheckResult("Authentication successful", True, f"via {s.tg_auth_method}"))
    except TigerGraphUnavailable as exc:
        category, detail = classify_network(s.tg_host, exc)
        # NETWORK means the host genuinely could not be reached at all -
        # authentication was never attempted, so "skipped" is honest.
        # Every other category (AUTHENTICATION, ENDPOINT, SERVER, ...)
        # means the request *did* reach TigerGraph and the token/auth
        # exchange itself failed - that must count as a real failure,
        # not a skip, regardless of which specific category it landed in.
        # (Caught live: a 500 Server Error was previously falling through
        # to "skipped", letting the whole check report ALL CHECKS PASSED
        # while authentication had actually failed.)
        host_reached = category != "NETWORK"
        results.append(
            CheckResult("Host reachable", host_reached, detail, "" if host_reached else category)
        )
        results.append(
            CheckResult(
                "Authentication successful",
                False if host_reached else None,
                detail if host_reached else "skipped - could not reach host",
                category if host_reached else "",
            )
        )
        for name in ("Graph accessible", "Schema accessible", "Read-only query successful"):
            results.append(CheckResult(name, None))
        return results
    except Exception as exc:
        results.append(CheckResult("Host reachable", False, f"{type(exc).__name__}: {exc}", "SDK"))
        for name in ("Authentication successful", "Graph accessible", "Schema accessible", "Read-only query successful"):
            results.append(CheckResult(name, None))
        return results

    try:
        graphs = c.connection.gsql("SHOW GRAPH *")
        graphs_text = str(graphs)
        # GSQL reports some failures (e.g. no graph exists yet) as plain
        # text in a 200-style response rather than raising - a bare
        # `True` here would put a checkmark next to a message that says
        # "fails". Downgrade those to informational rather than claim
        # success on a call that didn't actually list anything.
        looks_like_failure = any(
            phrase in graphs_text.lower()
            for phrase in ("semantic check fail", "no graph available", "error")
        )
        results.append(
            CheckResult("Graph listing", None if looks_like_failure else True, graphs_text[:150])
        )
    except Exception as exc:
        results.append(CheckResult("Graph listing", None, f"unsupported or restricted: {type(exc).__name__}"))

    try:
        schema = c.get_schema()
        vtypes = [v.get("Name") for v in schema.get("VertexTypes", [])]
        etypes = [e.get("Name") for e in schema.get("EdgeTypes", [])]
        results.append(CheckResult("Graph accessible", True, f"graph '{s.tg_graphname}'"))
        results.append(
            CheckResult(
                "Schema accessible",
                True,
                f"{len(vtypes)} vertex types, {len(etypes)} edge types"
                if (vtypes or etypes)
                else "0 vertex types, 0 edge types (empty graph)",
            )
        )
    except TigerGraphUnavailable as exc:
        msg = str(exc)
        lowered = msg.lower()
        if "does not exist" in lowered or "not found" in lowered:
            results.append(CheckResult("Graph accessible", False, msg, "GRAPH"))
        elif any(k in lowered for k in ("401", "403", "authoriz")):
            results.append(CheckResult("Graph accessible", False, msg, "AUTHENTICATION"))
        else:
            results.append(CheckResult("Graph accessible", False, msg, "SERVER"))
        results.append(CheckResult("Schema accessible", None))
        results.append(CheckResult("Read-only query successful", None))
        return results
    except Exception as exc:
        results.append(CheckResult("Graph accessible", False, f"{type(exc).__name__}: {exc}", "SDK"))
        results.append(CheckResult("Schema accessible", None))
        results.append(CheckResult("Read-only query successful", None))
        return results

    try:
        counts = c.vertex_counts()
        total = sum(counts.values()) if counts else 0
        results.append(
            CheckResult(
                "Read-only query successful",
                True,
                f"getVertexCount('*') -> {len(counts)} type(s), {total:,} vertices total",
            )
        )
    except TigerGraphQueryError as exc:
        results.append(CheckResult("Read-only query successful", False, str(exc), "SERVER"))
    except Exception as exc:
        results.append(CheckResult("Read-only query successful", False, f"{type(exc).__name__}: {exc}", "SDK"))

    return results


def all_passed(results: list[CheckResult]) -> bool:
    return all(r.ok is not False for r in results)
