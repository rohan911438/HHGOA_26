"""Phase 1 - TigerGraph MCP live test.

Starts the official tigergraph-mcp server as a stdio subprocess using the
same credentials the rest of this app uses, and verifies:

  1. MCP executable resolves and the server starts
  2. it authenticates against the same live TigerGraph instance
  3. tools are discoverable
  4. the server's schema can be retrieved through an MCP tool call
  5. a read-only query works through an MCP tool call (vertex count)
  6. destructive tools (drop_graph, clear_graph_data, ...) are NOT exposed
     - the agent must never be able to reach them

Never mutates data. Exits non-zero on any failed check.

Usage:
    python scripts/test_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    _UNICODE_OK = True
except (AttributeError, ValueError, OSError):
    _UNICODE_OK = False

from app.config import get_settings  # noqa: E402
from app.mcp.config import ALWAYS_BLOCKED, build_server_config, missing_requirements  # noqa: E402

CHECK, CROSS = ("✓", "✗") if _UNICODE_OK else ("[OK]", "[X]")


def line(ok: bool, name: str, detail: str) -> str:
    return f"  {CHECK if ok else CROSS} {name} - {detail}"


async def run() -> bool:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    settings = get_settings()
    results: list[bool] = []

    missing = missing_requirements(settings)
    if missing:
        print(line(False, "Configuration", f"missing: {', '.join(missing)}"))
        return False
    print(line(True, "Configuration", f"host + {settings.tg_auth_method} credential present"))

    cfg = build_server_config(settings, read_only=True)
    print(line(True, "MCP executable resolved", cfg.command))

    params = StdioServerParameters(command=cfg.command, args=cfg.args, env=cfg.env)

    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=30)
                results.append(True)
                print(line(True, "MCP server started + initialized", "handshake ok"))

                tools_result = await asyncio.wait_for(session.list_tools(), timeout=15)
                tool_names = {t.name for t in tools_result.tools}
                results.append(bool(tool_names))
                print(line(bool(tool_names), "Tools discoverable", f"{len(tool_names)} tool(s) exposed"))

                # ---- destructive tools must NOT be reachable -----------------
                blocked_present = {b for b in ALWAYS_BLOCKED if any(b in t for t in tool_names)}
                ok = not blocked_present
                results.append(ok)
                print(
                    line(
                        ok,
                        "Destructive tools blocked",
                        "none of the always-blocked tools are exposed"
                        if ok
                        else f"LEAKED: {blocked_present}",
                    )
                )

                # ---- schema retrieval ------------------------------------------
                schema_tool = next((t for t in tool_names if t.endswith("get_graph_schema")), None)
                if schema_tool:
                    try:
                        r = await asyncio.wait_for(
                            session.call_tool(schema_tool, {"graph_name": settings.tg_graphname}), timeout=30
                        )
                        text = "".join(c.text for c in r.content if hasattr(c, "text"))
                        ok = bool(text) and not r.is_error
                        results.append(ok)
                        print(line(ok, "Schema retrieval via MCP", text[:150].replace("\n", " ")))
                    except Exception as exc:
                        results.append(False)
                        print(line(False, "Schema retrieval via MCP", f"{type(exc).__name__}: {exc}"))
                else:
                    results.append(False)
                    print(line(False, "Schema retrieval via MCP", "get_graph_schema tool not found"))

                # ---- read-only query: vertex count -----------------------------
                count_tool = next((t for t in tool_names if t.endswith("get_vertex_count")), None)
                if count_tool:
                    try:
                        r = await asyncio.wait_for(
                            session.call_tool(count_tool, {"graph_name": settings.tg_graphname}), timeout=30
                        )
                        text = "".join(c.text for c in r.content if hasattr(c, "text"))
                        ok = bool(text) and not r.is_error
                        results.append(ok)
                        print(line(ok, "Read-only query via MCP", text[:150].replace("\n", " ")))
                    except Exception as exc:
                        results.append(False)
                        print(line(False, "Read-only query via MCP", f"{type(exc).__name__}: {exc}"))
                else:
                    results.append(False)
                    print(line(False, "Read-only query via MCP", "get_vertex_count tool not found"))

    except Exception as exc:
        results.append(False)
        print(line(False, "MCP server started + initialized", f"{type(exc).__name__}: {exc}"))

    return all(results) if results else False


def main() -> int:
    print("\nTigerGraph MCP live test")
    print("=" * 70)
    healthy = asyncio.run(run())
    print("=" * 70)
    print(f"Result: {'ALL CHECKS PASSED' if healthy else 'SOME CHECKS FAILED'}\n")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
