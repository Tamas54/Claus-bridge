import asyncio
from typing import Dict, Any
from pyramid.context_builder import build_agent_context
from pyramid.governance import store_result


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
                task_title=task_title
            )

        return agent_id, result

    coroutines = [
        run_single_agent(agent_id, task)
        for agent_id, task in agent_tasks.items()
    ]

    results = await asyncio.gather(*coroutines, return_exceptions=True)

    output = {}
    for item in results:
        if isinstance(item, Exception):
            continue
        agent_id, result = item
        output[agent_id] = result

    return output
