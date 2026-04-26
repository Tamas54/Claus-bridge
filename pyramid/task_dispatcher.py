import asyncio
import logging
from typing import Dict, Any
from pyramid.context_builder import build_agent_context
from pyramid.governance import store_result

logger = logging.getLogger(__name__)


async def dispatch_parallel_tasks(
    agent_tasks: Dict[str, dict],
    task_title: str = "",
    shared_context: str = "",
    call_agent_func=None
) -> Dict[str, Any]:
    """
    Párhuzamosan kiad eltérő feladatokat különböző agenteknek.

    Args:
        agent_tasks: {
            "kimi": {"prompt": "...", "system_prompt": None, "max_tokens": 3000, "temperature": 0.6},
            "deepseek": { ... },
        }
        task_title: A feladat címe (governance-hoz és RAG-hoz)
        shared_context: Opcionális extra kontextus
        call_agent_func: A server.py adja át a tényleges SiliconFlow hívó függvényt
    """

    async def run_single_agent(agent_id: str, task: dict) -> tuple:
        full_system_prompt = build_agent_context(
            agent_id=agent_id,
            custom_system_prompt=task.get("system_prompt"),
            include_shared_memory=True,
            include_rag=True
        )

        result = await call_agent_func(
            model=agent_id,
            prompt=task["prompt"],
            system_prompt=full_system_prompt,
            max_tokens=task.get("max_tokens", 3000),
            temperature=task.get("temperature", 0.7)
        )

        if result and "response" in result:
            store_result(
                content=result["response"],
                agent_id=agent_id,
                task_title=task_title,
                force_shared=True,
            )

        return agent_id, result

    agent_ids = list(agent_tasks.keys())
    coroutines = [run_single_agent(agent_id, agent_tasks[agent_id]) for agent_id in agent_ids]

    results = await asyncio.gather(*coroutines, return_exceptions=True)

    output = {}
    for agent_id, item in zip(agent_ids, results):
        if isinstance(item, Exception):
            logger.error("Dispatch agent %s failed: %s: %s", agent_id, type(item).__name__, item)
            output[agent_id] = {
                "response": f"ERROR: {type(item).__name__}: {item}",
                "tokens": {"prompt": 0, "completion": 0},
            }
            continue
        aid, result = item
        output[aid] = result

    return output
