"""Configuration for the official TigerGraph MCP server.

The `tigergraph-mcp` package (v1.0.3) reads its TigerGraph connection from
the process environment using the unprefixed `TG_*` variables - the same
names this project already uses in `.env`. That means we launch the server
as a subprocess with our own environment and it connects to the same
Savanna database, with no duplicated credential handling.

Two details the package documents that are worth calling out:

  * The profile is selected by `TG_PROFILE` / `TG_DEFAULT_PROFILE`, not by
    `TG_MCP_PROFILE`. `TG_MCP_PROFILE` is this application's setting name,
    so `build_server_env()` translates it.
  * `TG_ALLOWED_TOOLS` / `TG_BLOCKED_TOOLS` accept category and capability
    selectors (`read-only`, `destructive`, `schema`, `query`, ...). We use
    these to enforce the project's safety rule: an investigating agent gets
    read-only graph access and can never reach `drop_graph`.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.config import BACKEND_ROOT, Settings, get_settings

# Env var names the MCP server reads for TigerGraph connectivity.
TG_TOPOLOGY_KEYS = (
    "TG_HOST",
    "TG_GRAPHNAME",
    "TG_RESTPP_PORT",
    "TG_GS_PORT",
    "TG_SSL_PORT",
    "TG_TGCLOUD",
    "TG_CERT_PATH",
)
TG_IDENTITY_KEYS = (
    "TG_USERNAME",
    "TG_PASSWORD",
    "TG_SECRET",
    "TG_API_TOKEN",
    "TG_JWT_TOKEN",
)

# The investigation agent reads the graph. It never mutates it.
# Schema creation and data loading run through scripts, not the agent.
INVESTIGATION_TOOLSET = "read-only"

# Never exposed to the agent under any configuration.
ALWAYS_BLOCKED = (
    "drop_graph",
    "clear_graph_data",
    "drop_all_data_sources",
)


@dataclass
class MCPServerConfig:
    """Everything needed to launch one MCP server process."""

    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8001
    cwd: str = str(BACKEND_ROOT)

    def describe(self) -> dict[str, object]:
        """Launch description with no credential values."""
        return {
            "command": self.command,
            "args": self.args,
            "transport": self.transport,
            "cwd": self.cwd,
            "env_keys": sorted(self.env.keys()),
            "allowed_tools": self.env.get("TG_ALLOWED_TOOLS"),
            "blocked_tools": self.env.get("TG_BLOCKED_TOOLS"),
        }


def find_server_executable() -> tuple[str, list[str]]:
    """Locate the MCP server entry point.

    Prefers the console script installed next to the running interpreter,
    then anything on PATH, then `python -m tigergraph_mcp.main`.
    """
    scripts_dir = Path(sys.executable).parent
    for name in ("tigergraph-mcp.exe", "tigergraph-mcp"):
        candidate = scripts_dir / name
        if candidate.exists():
            return str(candidate), []

    found = shutil.which("tigergraph-mcp")
    if found:
        return found, []

    return sys.executable, ["-m", "tigergraph_mcp.main"]


def build_server_env(
    settings: Settings | None = None,
    *,
    read_only: bool = True,
    inherit: bool = True,
) -> dict[str, str]:
    """Build the environment the MCP subprocess is launched with.

    Only non-empty credentials are passed through, so an unset optional
    credential does not become an empty string the server tries to use.
    """
    s = settings or get_settings()
    env: dict[str, str] = dict(os.environ) if inherit else {}

    env["TG_HOST"] = s.tg_host
    env["TG_GRAPHNAME"] = s.tg_graphname
    env["TG_RESTPP_PORT"] = str(s.tg_restpp_port)
    env["TG_GS_PORT"] = str(s.tg_gs_port)
    env["TG_SSL_PORT"] = str(s.tg_ssl_port)
    env["TG_TGCLOUD"] = "true" if s.tg_tgcloud else "false"
    if s.tg_cert_path:
        env["TG_CERT_PATH"] = s.tg_cert_path

    if s.tg_username:
        env["TG_USERNAME"] = s.tg_username
    for key, secret in (
        ("TG_PASSWORD", s.tg_password),
        ("TG_SECRET", s.tg_secret),
        ("TG_API_TOKEN", s.tg_api_token),
        ("TG_JWT_TOKEN", s.tg_jwt_token),
    ):
        value = secret.get_secret_value()
        if value:
            env[key] = value
        else:
            env.pop(key, None)

    # Our setting name -> the server's setting name.
    env["TG_PROFILE"] = s.tg_mcp_profile
    env["TG_DEFAULT_PROFILE"] = s.tg_mcp_profile

    if read_only or s.tg_read_only:
        env["TG_ALLOWED_TOOLS"] = INVESTIGATION_TOOLSET
    else:
        env.pop("TG_ALLOWED_TOOLS", None)
    env["TG_BLOCKED_TOOLS"] = ",".join(ALWAYS_BLOCKED)

    return env


def build_server_config(
    settings: Settings | None = None, *, read_only: bool = True
) -> MCPServerConfig:
    s = settings or get_settings()
    command, args = find_server_executable()

    if s.tg_mcp_transport == "http":
        args = args + ["--transport", "http", "--host", s.tg_mcp_host, "--port", str(s.tg_mcp_port)]

    return MCPServerConfig(
        command=command,
        args=args,
        env=build_server_env(s, read_only=read_only),
        transport=s.tg_mcp_transport,
        host=s.tg_mcp_host,
        port=s.tg_mcp_port,
    )


def missing_requirements(settings: Settings | None = None) -> list[str]:
    """Config the MCP server needs but does not have. Empty means ready."""
    s = settings or get_settings()
    missing: list[str] = []
    if not s.tg_host_set:
        missing.append("TG_HOST")
    if not s.tg_graph_set:
        missing.append("TG_GRAPHNAME")
    if s.tg_auth_method == "none":
        missing.append("one of TG_API_TOKEN / TG_JWT_TOKEN / TG_SECRET / TG_PASSWORD")
    return missing
