"""TigerGraph connection management.

Wraps pyTigerGraph so the rest of the application never touches raw
connection details. Responsibilities:

  * build a connection from Settings, preferring token auth
  * lazily authenticate and cache the connection
  * enforce read-only mode when configured
  * translate driver errors into typed, non-leaking exceptions
  * never raise from health checks - return a structured status instead
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings, get_settings
from app.logging import InvestigationEvent, get_logger, log_event

logger = get_logger(__name__)


# ---------------------------------------------------------------- errors


class TigerGraphError(RuntimeError):
    """Base class for all TigerGraph failures surfaced to the app."""


class TigerGraphNotConfigured(TigerGraphError):
    """Credentials or host are missing."""


class TigerGraphUnavailable(TigerGraphError):
    """The database could not be reached or authenticated."""


class TigerGraphQueryError(TigerGraphError):
    """A query was rejected or failed during execution."""


class TigerGraphReadOnlyViolation(TigerGraphError):
    """A mutating statement was attempted while TG_READ_ONLY=true."""


# ---------------------------------------------------------------- status


@dataclass
class ConnectionStatus:
    connected: bool
    auth_method: str
    host: str | None = None
    graph: str | None = None
    version: str | None = None
    latency_ms: float | None = None
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "auth_method": self.auth_method,
            "host": self.host,
            "graph": self.graph,
            "version": self.version,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "details": self.details,
        }


# ---------------------------------------------------------------- client

_WRITE_KEYWORDS = (
    "create ",
    "drop ",
    "delete ",
    "alter ",
    "insert ",
    "update ",
    "upsert ",
    "clear ",
    "install ",
)


class TigerGraphClient:
    """Thread-safe lazy wrapper around a pyTigerGraph connection."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._conn: Any = None
        self._lock = threading.Lock()
        self._authenticated = False

    # ------------------------------------------------------ construction

    def _build(self) -> Any:
        s = self.settings
        if not s.tg_host:
            raise TigerGraphNotConfigured(
                "TG_HOST is not set. Copy .env.example to .env and fill in the "
                "Savanna endpoint from your workspace."
            )
        if not s.tg_graphname:
            raise TigerGraphNotConfigured("TG_GRAPHNAME is not set.")

        try:
            import pyTigerGraph as tg
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise TigerGraphNotConfigured(f"pyTigerGraph is not installed: {exc}") from exc

        kwargs: dict[str, Any] = {
            "host": s.tg_host,
            "graphname": s.tg_graphname,
            "restppPort": str(s.tg_restpp_port),
            "gsPort": str(s.tg_gs_port),
            "sslPort": s.tg_ssl_port,
            "tgCloud": s.tg_tgcloud,
        }
        if s.tg_username:
            kwargs["username"] = s.tg_username
        if s.tg_password.get_secret_value():
            kwargs["password"] = s.tg_password.get_secret_value()
        if s.tg_secret.get_secret_value():
            kwargs["gsqlSecret"] = s.tg_secret.get_secret_value()
        if s.tg_api_token.get_secret_value():
            kwargs["apiToken"] = s.tg_api_token.get_secret_value()
        if s.tg_jwt_token.get_secret_value():
            kwargs["jwtToken"] = s.tg_jwt_token.get_secret_value()
        if s.tg_cert_path:
            kwargs["certPath"] = s.tg_cert_path

        return tg.TigerGraphConnection(**kwargs)

    def _authenticate(self, conn: Any) -> None:
        """Obtain a REST++ token when we only hold a secret or password.

        Token and JWT auth need no extra step. With a GSQL secret we ask the
        driver to mint a token. With username/password on Savanna the driver
        handles token exchange itself, so we only probe the connection.
        """
        s = self.settings
        method = s.tg_auth_method

        if method in ("api_token", "jwt"):
            self._authenticated = True
            return

        if method == "secret":
            try:
                conn.getToken(s.tg_secret.get_secret_value())
                self._authenticated = True
                return
            except Exception as exc:
                raise TigerGraphUnavailable(
                    f"Could not mint a REST++ token from TG_SECRET: {type(exc).__name__}: {exc}"
                ) from exc

        if method == "password":
            # Let the driver negotiate. Some Savanna deployments accept
            # username/password directly against REST++.
            try:
                conn.getToken()
                self._authenticated = True
                return
            except Exception:
                # Not fatal: many 4.x deployments authenticate per request.
                self._authenticated = True
                return

        raise TigerGraphNotConfigured(
            "No TigerGraph credential available. Set one of TG_API_TOKEN, "
            "TG_JWT_TOKEN, TG_SECRET or TG_PASSWORD."
        )

    # ------------------------------------------------------ access

    @property
    def connection(self) -> Any:
        if self._conn is None:
            with self._lock:
                if self._conn is None:
                    conn = self._build()
                    self._authenticate(conn)
                    self._conn = conn
        return self._conn

    def reset(self) -> None:
        with self._lock:
            self._conn = None
            self._authenticated = False

    # ------------------------------------------------------ operations

    def echo(self) -> str:
        try:
            return self.connection.echo()
        except TigerGraphError:
            raise
        except Exception as exc:
            raise TigerGraphUnavailable(f"echo failed: {type(exc).__name__}: {exc}") from exc

    def get_version(self) -> str | None:
        try:
            return self.connection.getVer()
        except Exception:
            return None

    def get_schema(self) -> dict[str, Any]:
        try:
            return self.connection.getSchema(force=True)
        except Exception as exc:
            raise TigerGraphUnavailable(
                f"schema retrieval failed: {type(exc).__name__}: {exc}"
            ) from exc

    def vertex_counts(self) -> dict[str, int]:
        try:
            counts = self.connection.getVertexCount("*")
            return counts if isinstance(counts, dict) else {}
        except Exception as exc:
            raise TigerGraphQueryError(
                f"vertex count failed: {type(exc).__name__}: {exc}"
            ) from exc

    def edge_counts(self) -> dict[str, int]:
        try:
            counts = self.connection.getEdgeCount("*")
            return counts if isinstance(counts, dict) else {}
        except Exception as exc:
            raise TigerGraphQueryError(f"edge count failed: {type(exc).__name__}: {exc}") from exc

    def gsql(self, statement: str, *, allow_write: bool = False) -> str:
        """Run a GSQL statement. Write statements need allow_write=True."""
        lowered = statement.strip().lower()
        is_write = any(lowered.startswith(k) for k in _WRITE_KEYWORDS)

        if is_write and self.settings.tg_read_only:
            raise TigerGraphReadOnlyViolation(
                "TG_READ_ONLY=true - refusing to run a mutating GSQL statement."
            )
        if is_write and not allow_write:
            raise TigerGraphReadOnlyViolation(
                "This GSQL statement mutates the database; call with allow_write=True."
            )

        started = time.perf_counter()
        try:
            result = self.connection.gsql(statement)
        except Exception as exc:
            raise TigerGraphQueryError(f"GSQL failed: {type(exc).__name__}: {exc}") from exc
        elapsed = (time.perf_counter() - started) * 1000
        log_event(
            logger,
            InvestigationEvent.GRAPH_QUERY,
            "gsql executed",
            statement_preview=statement.strip()[:120],
            write=is_write,
            latency_ms=round(elapsed, 1),
        )
        return result if isinstance(result, str) else str(result)

    def run_query(self, name: str, params: dict[str, Any] | None = None) -> Any:
        """Run an installed GSQL query by name."""
        started = time.perf_counter()
        log_event(
            logger,
            InvestigationEvent.GRAPH_QUERY,
            f"running {name}",
            query=name,
            params=params or {},
        )
        try:
            result = self.connection.runInstalledQuery(
                name, params=params or {}, timeout=self.settings.tg_timeout_seconds * 1000
            )
        except Exception as exc:
            log_event(
                logger,
                InvestigationEvent.TOOL_FAILED,
                f"query {name} failed",
                query=name,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise TigerGraphQueryError(
                f"installed query '{name}' failed: {type(exc).__name__}: {exc}"
            ) from exc
        elapsed = (time.perf_counter() - started) * 1000
        log_event(
            logger,
            InvestigationEvent.GRAPH_RESULT,
            f"{name} returned",
            query=name,
            latency_ms=round(elapsed, 1),
        )
        return result

    # ------------------------------------------------------ health

    def health(self) -> ConnectionStatus:
        """Never raises. Returns a structured status for /health/tigergraph."""
        s = self.settings
        status = ConnectionStatus(
            connected=False,
            auth_method=s.tg_auth_method,
            host=s.tg_host or None,
            graph=s.tg_graphname or None,
        )
        if not s.tg_configured:
            status.error = (
                "TigerGraph is not configured. Required: TG_HOST, TG_GRAPHNAME and one "
                "of TG_API_TOKEN / TG_JWT_TOKEN / TG_SECRET / TG_PASSWORD."
            )
            return status

        started = time.perf_counter()
        try:
            echoed = self.echo()
            status.connected = True
            status.latency_ms = round((time.perf_counter() - started) * 1000, 1)
            status.version = self.get_version()
            status.details["echo"] = str(echoed)[:120]
        except Exception as exc:
            status.latency_ms = round((time.perf_counter() - started) * 1000, 1)
            status.error = f"{type(exc).__name__}: {exc}"
            log_event(
                logger,
                InvestigationEvent.GRAPH_UNAVAILABLE,
                "TigerGraph health check failed",
                error=status.error,
            )
        return status


_client: TigerGraphClient | None = None


def get_client(settings: Settings | None = None) -> TigerGraphClient:
    global _client
    if _client is None or settings is not None:
        _client = TigerGraphClient(settings)
    return _client
