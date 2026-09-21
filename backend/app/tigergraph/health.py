"""TigerGraph health and readiness probes.

Every probe returns a structured result and never raises, so health
endpoints degrade rather than fail. A probe that cannot run reports
status "unavailable" with a reason - it never reports success on
missing data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.config import Settings, get_settings
from app.tigergraph.client import TigerGraphClient, get_client


@dataclass
class ProbeResult:
    name: str
    status: str  # "ok" | "unavailable" | "not_configured" | "empty"
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def probe_configuration(settings: Settings | None = None) -> ProbeResult:
    s = settings or get_settings()
    missing: list[str] = []
    if not s.tg_host_set:
        missing.append("TG_HOST")
    if not s.tg_graph_set:
        missing.append("TG_GRAPHNAME")
    if s.tg_auth_method == "none":
        missing.append("TG_API_TOKEN | TG_JWT_TOKEN | TG_SECRET | TG_PASSWORD")

    if missing:
        return ProbeResult(
            name="configuration",
            status="not_configured",
            detail="Missing: " + ", ".join(missing),
            data={"missing": missing},
        )
    return ProbeResult(
        name="configuration",
        status="ok",
        detail=f"auth via {s.tg_auth_method}",
        data={"auth_method": s.tg_auth_method, "host": s.tg_host, "graph": s.tg_graphname},
    )


def probe_connection(client: TigerGraphClient | None = None) -> ProbeResult:
    c = client or get_client()
    status = c.health()
    if status.connected:
        return ProbeResult(
            name="connection",
            status="ok",
            detail=f"{status.latency_ms} ms, version {status.version or 'unknown'}",
            data=status.as_dict(),
        )
    if not c.settings.tg_configured:
        return ProbeResult(
            name="connection", status="not_configured", detail=status.error or "", data={}
        )
    return ProbeResult(
        name="connection", status="unavailable", detail=status.error or "", data=status.as_dict()
    )


def probe_schema(client: TigerGraphClient | None = None) -> ProbeResult:
    c = client or get_client()
    if not c.settings.tg_configured:
        return ProbeResult(name="schema", status="not_configured", detail="TigerGraph not configured")
    try:
        schema = c.get_schema()
    except Exception as exc:
        return ProbeResult(name="schema", status="unavailable", detail=f"{type(exc).__name__}: {exc}")

    vertices = [v.get("Name") for v in schema.get("VertexTypes", [])]
    edges = [e.get("Name") for e in schema.get("EdgeTypes", [])]
    if not vertices and not edges:
        return ProbeResult(
            name="schema",
            status="empty",
            detail="Graph exists but has no vertex or edge types yet",
            data={"vertex_types": [], "edge_types": []},
        )
    return ProbeResult(
        name="schema",
        status="ok",
        detail=f"{len(vertices)} vertex types, {len(edges)} edge types",
        data={"vertex_types": vertices, "edge_types": edges},
    )


def probe_counts(client: TigerGraphClient | None = None) -> ProbeResult:
    c = client or get_client()
    if not c.settings.tg_configured:
        return ProbeResult(name="counts", status="not_configured", detail="TigerGraph not configured")
    try:
        vcounts = c.vertex_counts()
    except Exception as exc:
        return ProbeResult(name="counts", status="unavailable", detail=f"{type(exc).__name__}: {exc}")

    try:
        ecounts = c.edge_counts()
    except Exception:
        ecounts = {}

    total_v = sum(vcounts.values()) if vcounts else 0
    if total_v == 0:
        return ProbeResult(
            name="counts",
            status="empty",
            detail="Graph has no vertices loaded",
            data={"vertices": vcounts, "edges": ecounts},
        )
    return ProbeResult(
        name="counts",
        status="ok",
        detail=f"{total_v} vertices across {len(vcounts)} types",
        data={"vertices": vcounts, "edges": ecounts},
    )


def full_report(client: TigerGraphClient | None = None) -> dict[str, Any]:
    """Run every probe in order. Later probes are skipped once one fails."""
    c = client or get_client()
    results: list[ProbeResult] = []

    cfg = probe_configuration(c.settings)
    results.append(cfg)

    if cfg.ok:
        conn = probe_connection(c)
        results.append(conn)
        if conn.ok:
            results.append(probe_schema(c))
            results.append(probe_counts(c))

    healthy = all(r.status in ("ok", "empty") for r in results)
    return {
        "healthy": healthy,
        "probes": [r.as_dict() for r in results],
    }
