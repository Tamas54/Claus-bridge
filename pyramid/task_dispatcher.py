import asyncio
import logging
from typing import Dict, Any
from pyramid.context_builder import build_agent_context
from pyramid.governance import store_result

logger = logging.getLogger(__name__)


# Ugyanaz a lista, mint a server.py TEXT_MARKER_TOKENS-e: azok a szövegdarabok,
# amikkel egy modell a tool-hívását TARTALOMKÉNT írja ki a rendes `tool_calls`
# mező helyett. Kimi K2.7 és DeepSeek-V4-Pro rendszeresen ezt teszi.
#
# Azért van itt MÁSODPÉLDÁNY, mert a server.py importálja a pyramidot, tehát
# visszafelé nem importálhatunk. A két listát a
# `tests/test_dispatch_tool_call.py` paritás-tesztje őrzi — ha valaki csak az
# egyiket bővíti, a teszt pirosra vált. (A mintát az OpenMausBot
# `electron/diagnostics.mjs` ↔ `server/config.ts` paritás-tesztje adta, ami
# ma pontosan ezt a hibát kapta el rajtam.)
TEXT_MARKER_TOKENS = (
    "<|tool_call",
    "<｜｜DSML｜｜",
    "<｜DSML｜",
    "function_calls>",
    "tool_calls_section",
)


def looks_like_unhandled_tool_call(text: str) -> str | None:
    """A megtalált marker, ha a szöveg kiírt tool-hívást tartalmaz — különben None.

    EZ A 395-ÖS INCIDENS ELLENSZERE. Ami történt: dispatch módban a keresésre
    utasított agent nem tudott keresni, mert azon az útvonalon nincs eszköz.
    A modell erre KIÍRTA a tool-hívás blokkját sima szövegként, a hívó pedig
    azt hitte, dolgozott. A szintézis-réteg utána kipótolta a hiányt kitalált
    adattal ÉS HAMIS FORRÁSMEGJELÖLÉSSEL.

    A néma hiba drágább volt, mint a hiányzó képesség. Ezért ez a felismerés
    nem javít semmit — HANGOSSÁ teszi. Egy futás, ami tool-hívást ír ki, de
    senki nem hajtotta végre, NEM SIKERES FUTÁS.
    """
    if not text:
        return None
    for marker in TEXT_MARKER_TOKENS:
        if marker in text:
            return marker
    return None


# A `_call_agent` / `_run_single_agent` útvonalak tipizálatlan hibái: a
# tartalom maga kezdődik ezekkel. Nincs `error` kulcsuk, de bizonyítottan
# hibásak — a felszínen ezeket sem szabad sikernek látni.
ERROR_RESPONSE_PREFIXES = ("ERROR:", "TIMEOUT", "(no response)")


def result_error_code(result) -> str:
    """A tipizált hibakód egy dispatch-eredményből; "" ha a futás rendben volt.

    S-002 MÁSODIK FELE: a `dispatch_parallel_tasks` már ma is ad `error`
    kulcsot (`unhandled_tool_call`, vagy a kivétel típusneve), de a hívó
    eddig CSAK a `response`-t olvasta ki — a tipizált rész a földre esett,
    és a hibás futás sikeresként jelent meg egy réteggel feljebb.

    Ez a függvény az EGY gazda arra a kérdésre, hogy „hibás volt-e ez az
    eredmény": a tipizált kulcs elsőbbséget élvez, de a csak-szövegben
    jelzett hiba sem tűnhet el (`error_response`).
    """
    resp = None
    if isinstance(result, dict):
        code = str(result.get("error") or "").strip()
        if code:
            return code
        resp = result.get("response")
    else:
        resp = result
    if isinstance(resp, str) and resp.strip().startswith(ERROR_RESPONSE_PREFIXES):
        return "error_response"
    if resp is None or (isinstance(resp, str) and not resp.strip()):
        return "empty_response"
    return ""


def partition_results(results) -> tuple[dict, dict]:
    """(usable, failed) szétválasztás egy dispatch-eredményhalmazon.

    `usable`: {agent_id: eredmény} — ezek mehetnek tovább (tárolás, szintézis).
    `failed`: {agent_id: hibakód} — ezek NEM. A hívó ebből tudja eldönteni,
    hogy a futás egészben, részben vagy egyáltalán nem hibás.
    """
    usable, failed = {}, {}
    for agent_id, result in (results or {}).items():
        code = result_error_code(result)
        if code:
            failed[agent_id] = code
        else:
            usable[agent_id] = result
    return usable, failed


def all_failed(results) -> bool:
    """Igaz, ha volt eredmény, és EGYETLEN agent sem adott értékelhetőt."""
    usable, failed = partition_results(results)
    return bool(failed) and not usable


async def dispatch_parallel_tasks(
    agent_tasks: Dict[str, dict],
    task_title: str = "",
    shared_context: str = "",
    call_agent_func=None,
    run_with_tools_func=None,
) -> Dict[str, Any]:
    """
    Párhuzamosan kiad eltérő feladatokat különböző agenteknek.

    Args:
        agent_tasks: {
            "kimi": {"prompt": "...", "system_prompt": None, "max_tokens": 3000,
                     "temperature": 0.6, "use_tools": True},
            "deepseek": { ... },
        }
        task_title: A feladat címe (governance-hoz és RAG-hoz)
        shared_context: Opcionális extra kontextus
        call_agent_func: eszköz NÉLKÜLI hívó (a régi út)
        run_with_tools_func: eszközös hívó — a server.py `_run_agent_with_tools`-a.
            Aláírás: (model_id, messages, max_rounds=..., max_tokens=...) -> str

    ESZKÖZ MINDEN ÚTVONALON (Kommandant-követelmény, 2026-08-24):
    „Ami broadcast módban elérhető, legyen elérhető dispatch módban is."
    Egy agent, amelyik ÉPÍT valamit, minden üzemmódban eszközt igényel — ez
    nem kényelmi kérdés, hanem a képesség maga.

    A feladatonkénti `use_tools` alapértéke True. Ha nincs `run_with_tools_func`
    injektálva, az eszköztelen útra esünk vissza — DE a kimenetet átvizsgáljuk
    kiírt tool-hívásra, és ha van, a futás HIBÁS lesz, nem „sikeres".
    """

    async def run_single_agent(agent_id: str, task: dict) -> tuple:
        full_system_prompt = build_agent_context(
            agent_id=agent_id,
            custom_system_prompt=task.get("system_prompt"),
            include_shared_memory=True,
            include_rag=True,
            minimal=task.get("minimal", False),
        )
        wants_tools = task.get("use_tools", True)
        max_tokens = task.get("max_tokens", 3000)

        if wants_tools and run_with_tools_func is not None:
            # Ugyanaz a hurok, amit a broadcast és a szintézis is használ:
            # a modell hívhat, mi végrehajtjuk, az eredményt visszaadjuk neki.
            messages = [
                {"role": "system", "content": full_system_prompt},
                {"role": "user", "content": task["prompt"]},
            ]
            text = await run_with_tools_func(
                agent_id,
                messages,
                max_rounds=task.get("max_rounds", 4),
                max_tokens=max_tokens,
            )
            result = {"response": text, "tokens": {"prompt": 0, "completion": 0}, "tools": True}
        else:
            result = await call_agent_func(
                model=agent_id,
                prompt=task["prompt"],
                system_prompt=full_system_prompt,
                max_tokens=max_tokens,
                temperature=task.get("temperature", 0.7),
            )
            if isinstance(result, dict):
                result.setdefault("tools", False)

        # A KAPU: kiírt tool-hívás = hibás futás, bármelyik úton jött.
        # Az eszközös úton is nézzük — ott azt jelenti, hogy a hurok elfogyott
        # a körökből, és a modell utolsó szava egy végre nem hajtott hívás.
        response_text = (result or {}).get("response") if isinstance(result, dict) else None
        marker = looks_like_unhandled_tool_call(response_text or "")
        if marker:
            had_tools = bool(wants_tools and run_with_tools_func is not None)
            reason = (
                "a tool-hurok elfogyott a körökből, az utolsó válasz egy végre nem hajtott hívás"
                if had_tools
                else "ezen az útvonalon NINCS eszköz-dispatcher, a hívás sosem futott le"
            )
            logger.error(
                "Dispatch agent %s tool-hívást ÍRT KI (%r) — %s. A futás hibás.",
                agent_id, marker, reason,
            )
            # Nem tároljuk a governance-be, és nem adjuk vissza válaszként:
            # egy kitalált forrásokkal teli szöveg rosszabb, mint a hiány.
            return agent_id, {
                "response": (
                    f"ERROR: UnhandledToolCall: az agent eszközt hívott ({marker}), de {reason}. "
                    "A választ eldobtuk — a hiányt jelenteni kell, nem kipótolni."
                ),
                "tokens": {"prompt": 0, "completion": 0},
                "error": "unhandled_tool_call",
                "marker": marker,
                "tools": had_tools,
            }

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
                "error": type(item).__name__,
            }
            continue
        aid, result = item
        output[aid] = result

    return output
