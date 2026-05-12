"""Smoke test for the four new brave_* sub-agent tools on the Bridge dispatcher.

Verifies:
  1. brave_* tool defs are present in SUBAGENT_TOOL_DEFS (the agent tool pool)
  2. _dispatch_subagent_tool routes each brave_* name to the right brave-mcp-server call
  3. The production brave-mcp-server endpoint responds end-to-end

Does NOT call SiliconFlow — that's a separate cost. Use:
    BRAVE_MCP_URL=https://brave-mcp-server-production.up.railway.app/mcp \\
        python3 -m vertical_plugins.test_brave_tools_dispatch
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path


def setup_path():
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    os.chdir(root)
    os.environ.setdefault(
        "BRAVE_MCP_URL",
        "https://brave-mcp-server-production.up.railway.app/mcp",
    )


async def run():
    import server as srv  # noqa: E402

    print(f"BRAVE_MCP_ENABLED = {srv.BRAVE_MCP_ENABLED}")
    print(f"BRAVE_MCP_URL    = {srv.BRAVE_MCP_URL}")

    names = [td["function"]["name"] for td in srv.SUBAGENT_TOOL_DEFS]
    print("\nSUBAGENT_TOOL_DEFS tool names:")
    for n in names:
        marker = " ← NEW" if n in ("brave_search", "brave_scrape", "brave_login", "brave_action") else ""
        print(f"  • {n}{marker}")

    required = {"brave_search", "brave_scrape", "brave_login", "brave_action"}
    missing = required - set(names)
    assert not missing, f"Missing tools in SUBAGENT_TOOL_DEFS: {missing}"
    print("\n[OK] All four brave_* tools are in the agent tool pool.")

    # ── 1) brave_search ────────────────────────────────────────────────
    print("\n──── brave_search ────")
    r = await srv._dispatch_subagent_tool(
        "brave_search", {"query": "Eurostat HICP flash 2026 February", "limit": 3}
    )
    print(r[:600])
    assert "error" not in r[:100].lower() or "result" in r.lower(), "brave_search failed"

    # ── 2) brave_scrape ────────────────────────────────────────────────
    print("\n──── brave_scrape ────")
    r = await srv._dispatch_subagent_tool(
        "brave_scrape",
        {"url": "https://ec.europa.eu/eurostat/web/main/news/euro-indicators",
         "wait_time": 4000},
    )
    obj = json.loads(r.split("\n\n[_bridge_fetched_at:")[0])
    print(f"url={obj.get('url')}\ntitle={obj.get('title')[:120]}\nmd len={len(obj.get('markdown',''))}")
    assert obj.get("markdown"), "brave_scrape returned empty markdown"

    # ── 3) brave_login ─ validation only (don't actually send creds) ───
    print("\n──── brave_login (input-validation path) ────")
    r = await srv._dispatch_subagent_tool("brave_login", {"site": "bogus_site"})
    print(r)
    assert "site must be" in r, "brave_login validation message changed"
    r = await srv._dispatch_subagent_tool("brave_login", {"site": "gmail"})
    print(r)
    assert "username and password required" in r

    # ── 4) brave_action ─ no-login navigate+screenshot+wait sequence ───
    print("\n──── brave_action (navigate + wait + screenshot) ────")
    r = await srv._dispatch_subagent_tool(
        "brave_action",
        {
            "url": "https://example.com",
            "actions": [
                {"type": "wait", "ms": 500},
                {"type": "navigate", "url": "https://example.com"},
                {"type": "screenshot", "url": "https://example.com"},
            ],
        },
    )
    obj = json.loads(r.split("\n\n[_bridge_fetched_at:")[0])
    print(f"steps={obj.get('steps')} results=")
    for step in obj.get("results", []):
        # Trim the screenshot blob so the log stays readable.
        s = dict(step)
        if "screenshot_base64" in s:
            s["screenshot_base64"] = f"<base64 len={len(s['screenshot_base64'])}>"
        print(" ", s)
    ok_count = sum(1 for s in obj["results"] if s.get("ok") is True or s.get("type") == "wait")
    assert ok_count >= 2, f"brave_action: too few successful steps ({ok_count})"

    print("\n[OK] All four brave_* dispatcher branches operational.")


if __name__ == "__main__":
    setup_path()
    asyncio.run(run())
