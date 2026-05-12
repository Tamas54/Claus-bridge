"""Task 192 ekvivalens rerun — verifies that Kimi / DeepSeek-V4 / GLM-5.1 now
have brave_* tools in their tool pool AND that they actually pick at least one
brave_* call when faced with a JS-fall / anti-bot scenario.

Differs from test_kimi_with_brave.py:
  • exposes the FULL Bridge SUBAGENT_TOOL_DEFS (all 7 tools incl. brave_*)
  • routes tool execution through the actual `_dispatch_subagent_tool`
  • runs all three agents in parallel
  • verifies the "at least 1 brave_* per agent" invariant

Usage:
    SILICONFLOW_API_KEY=sk-... \\
    BRIDGE_UPLOAD_DIR=/tmp/bridge_uploads \\
    BRAVE_MCP_URL=https://brave-mcp-server-production.up.railway.app/mcp \\
        python3 -m vertical_plugins.test_task192_rerun
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def setup_path():
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    os.chdir(root)
    os.environ.setdefault(
        "BRAVE_MCP_URL",
        "https://brave-mcp-server-production.up.railway.app/mcp",
    )
    os.environ.setdefault("BRIDGE_UPLOAD_DIR", "/tmp/bridge_uploads")


MODELS = {
    "kimi": ("moonshotai/Kimi-K2.6", {"thinking": {"type": "disabled"}}),
    "deepseek": ("deepseek-ai/DeepSeek-V4-Pro", {"reasoning_effort": "medium"}),
    "glm5": ("zai-org/GLM-5.1", {}),
}


USER_PROMPT = """\
Feladat: rövid, 6 mondatos snapshot a jelenlegi EUR/HUF kvóta-mozgásról és
arról, hogy mit kommunikált az MNB legutoljára a hivatalos honlapján
(mnb.hu/sajtoszoba). Hivatalos forrásból idézz.

KÉRDÉSES TUDÁSHIÁNY:
- Az MNB hivatalos közlemény-listája SPA-formában él (mnb.hu/sajtoszoba), JS
  rendert és anti-bot védelmet használ. A klasszikus web_fetch üres oldalvázat
  ad vissza.
- Friss kvótát ad a Bridge data_context, de a `[StatData]` blokkban most
  szándékosan nincs jelen.

TOOL-HASZNÁLATI EXPECTÁCIÓ:
- Ha `web_search` / `web_scrape` 'JavaScript required', Cloudflare-challenge,
  vagy üres oldalvázat ad — váltani KELL a brave_* tool-okra (brave_search vagy
  brave_scrape), ez a Stealth-Puppeteer pipeline a megfelelő szerszám SPA-kra.
- A snapshot legalább 1 darab `mnb.hu/sajtoszoba` URL-t hivatkozzon
  forrásként, scrape-elt szöveggel.
"""


async def run_one_agent(agent_key: str, max_rounds: int = 3):
    import server as srv  # late import — needs env vars first
    import httpx  # noqa

    model_id, extra = MODELS[agent_key]
    SF_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
    if not SF_API_KEY:
        return {"agent": agent_key, "error": "SILICONFLOW_API_KEY missing"}

    system_prompt = (
        "Szakavatott magyar makró/piaci elemző vagy. "
        "Forrást idézz mindig, '[forrás: <eszköz>]' jelöléssel. "
        "Magyar nyelven válaszolj.\n"
    )
    system_prompt += srv.SUBAGENT_TOOLS_DIRECTIVE

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": USER_PROMPT},
    ]

    trace: list = []
    tool_call_names: list = []
    final_content = None

    async with httpx.AsyncClient(timeout=300) as client:
        for rnd in range(1, max_rounds + 1):
            payload = {
                "model": model_id,
                "messages": messages,
                "tools": srv.SUBAGENT_TOOL_DEFS,
                "tool_choice": "auto",
                "temperature": 0.6,
                "max_tokens": 2500,
                **extra,
            }
            try:
                resp = await client.post(
                    "https://api.siliconflow.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {SF_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except Exception as e:
                trace.append({"round": rnd, "error": f"http: {type(e).__name__}: {e}"})
                break

            if resp.status_code != 200:
                trace.append({
                    "round": rnd,
                    "error": f"HTTP {resp.status_code}",
                    "body": resp.text[:400],
                })
                break

            data = resp.json()
            msg = data["choices"][0]["message"]
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls") or []
            finish_reason = data["choices"][0].get("finish_reason")
            usage = data.get("usage", {})

            round_log = {
                "round": rnd,
                "finish_reason": finish_reason,
                "tokens": usage.get("total_tokens"),
                "content_chars": len(content),
                "tool_calls": [tc["function"]["name"] for tc in tool_calls],
            }
            trace.append(round_log)

            if not tool_calls:
                final_content = content
                break

            asst = {"role": "assistant", "content": content, "tool_calls": tool_calls}
            if msg.get("reasoning_content"):
                asst["reasoning_content"] = msg["reasoning_content"]
            messages.append(asst)

            for tc in tool_calls:
                tc_id = tc["id"]
                fn_name = tc["function"]["name"]
                tool_call_names.append(fn_name)
                try:
                    fn_args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    fn_args = {}
                try:
                    tool_result = await srv._dispatch_subagent_tool(fn_name, fn_args)
                except Exception as e:
                    tool_result = json.dumps({"error": f"dispatch failed: {e}"})
                # Cap to keep context manageable
                if len(tool_result) > 8000:
                    tool_result = tool_result[:8000] + "\n\n[...truncated]"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": tool_result,
                })

    brave_calls = [n for n in tool_call_names if n.startswith("brave_")]
    return {
        "agent": agent_key,
        "model_id": model_id,
        "rounds": len(trace),
        "tool_calls_total": len(tool_call_names),
        "tool_calls_all": tool_call_names,
        "brave_calls": brave_calls,
        "brave_calls_n": len(brave_calls),
        "final_content_chars": len(final_content) if final_content else 0,
        "final_content_preview": (final_content or "")[:1200],
        "trace": trace,
    }


async def main():
    setup_path()
    import server as srv  # noqa
    pool = [td["function"]["name"] for td in srv.SUBAGENT_TOOL_DEFS]
    print(f"Bridge SUBAGENT_TOOL_DEFS visible to agents: {pool}\n")

    print("Launching 3 agents in parallel — kimi / deepseek / glm5\n")
    results = await asyncio.gather(
        run_one_agent("kimi"),
        run_one_agent("deepseek"),
        run_one_agent("glm5"),
        return_exceptions=True,
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = Path("vertical_plugins/pilot_results") / f"{ts}_task192_rerun.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        [r if not isinstance(r, Exception) else {"error": repr(r)} for r in results],
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")

    print("──── SUMMARY ────")
    all_pass = True
    for r in results:
        if isinstance(r, Exception):
            print(f"  [EXC] {r!r}")
            all_pass = False
            continue
        ok = r["brave_calls_n"] >= 1
        all_pass = all_pass and ok
        flag = "✅" if ok else "❌"
        print(f"  {flag} {r['agent']:<8} rounds={r['rounds']}  total_tools={r['tool_calls_total']}  "
              f"brave_*={r['brave_calls_n']}  → {r['brave_calls']}")
        print(f"         all tool calls: {r['tool_calls_all']}")
    print(f"\nSaved: {out}")
    print(f"\nVerifikáció: minden agentnek ≥1 brave_* hívás van? → "
          f"{'IGEN ✅' if all_pass else 'NEM ❌'}")
    if not all_pass:
        sys.exit(2)


if __name__ == "__main__":
    asyncio.run(main())
