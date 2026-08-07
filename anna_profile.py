"""
AnnaKatheder Instance Profile — OPERATION KATHEDER
===================================================
A Kommandant másik unokahúga. Elsőéves, Újvidék.

Jogosultságban Rékáéval AZONOS szigor: email/naptár tiltva, böngésző-
vezérlés tiltva, a kereső szűk metszete nyitva.

A KÜLÖNBSÉG NEM JOGOSULTSÁGI, HANEM UTASÍTÁSI
----------------------------------------------
Réka és Anna között nem korkülönbség van, hanem kockázatkülönbség.
Réka MŰSZERKÉNT használja: gyorsít olyan munkát, amit tud, és kimenetet
vár. Anna TANUL: ellenállást kell kapnia, mert ami itt nem alakul ki
benne, az évekig nem fog látszani — a jegyei közben jók lesznek.

Ugyanaz a motor, ugyanaz a modell, fordított utasítás. A tiltás a
`KATHEDER_PROMPT`-ban él, nem itt: a permission-réteg tool-szinten véd,
a beadandó-szabály viszont tartalmi, azt a prompt hordozza.

Egy dolgot mégis a KÓD zár, nem a prompt: az „Alapos utánajárás" gomb
(`ai_task`) nála nincs kitéve. Egy promptot ki lehet beszélni; egy nem
létező gombot nem. Lásd `youngereka_access.CHAT_PROFILES`.
"""

from permissions import (
    PermissionProfile,
    Access,
    register_instance,
)
from youngereka_access import KATHEDER_PROMPT

ANNA_PROFILE = PermissionProfile(
    instance_id="AnnaKatheder",
    display_name="Anna",
    description="Kommandant unokahúga, elsőéves egyetemista Újvidéken — "
                "tanulótárs-hozzáférés, mínusz személyes email/naptár, "
                "böngésző-vezérlés és privát Claus-üzenetek.",

    tool_permissions={
        # --- SHARED MEMORY ---
        "list_memory":      Access.ALLOW,
        "read_memory":      Access.ALLOW,
        "search_memory":    Access.ALLOW,
        "write_memory":     Access.ALLOW,

        # --- MESSAGING: saját namespace ---
        "send_message":     Access.ALLOW,
        "read_messages":    Access.FILTERED,
        "read_new":         Access.FILTERED,
        "mark_read":        Access.ALLOW,

        # --- DISCUSSIONS / TASKS ---
        "list_discussions":     Access.ALLOW,
        "start_discussion":     Access.ALLOW,
        "add_to_discussion":    Access.ALLOW,
        "read_discussion":      Access.ALLOW,
        "resolve_discussion":   Access.ALLOW,
        "list_tasks":       Access.ALLOW,
        "create_task":      Access.ALLOW,
        "update_task":      Access.ALLOW,

        # --- MODELLEK ---
        "ai_query":         Access.ALLOW,
        "ai_task":          Access.ALLOW,
        "read_ai_task_results": Access.ALLOW,

        # --- FILES / META ---
        "upload_file":      Access.ALLOW,
        "get_status":       Access.ALLOW,
        "heartbeat":        Access.ALLOW,
        "log_session":      Access.ALLOW,

        # --- KERESÉS: a szűk metszet ---
        "search_web":       Access.ALLOW,
        "scrape_url":       Access.ALLOW,

        # --- BÖNGÉSZŐ-VEZÉRLÉS: TILTVA ---
        # Nem kereső, hanem böngésző perzisztens login-session-ökkel.
        "brave_login":            Access.DENY,
        "brave_session_action":   Access.DENY,
        "brave_navigate":         Access.DENY,
        "brave_mouse_control":    Access.DENY,
        "brave_visual_captcha":   Access.DENY,
        "brave_list_sessions":    Access.DENY,
        "brave_crawl":            Access.DENY,
        "brave_scrape":           Access.DENY,
        "brave_marked_snapshot":  Access.DENY,
        "brave_visual_inspect":   Access.DENY,
        "brave_clear_sessions":   Access.DENY,

        # --- EMAIL & NAPTÁR: TILTVA ---
        "capture_gmail_poll":       Access.DENY,
        "capture_send_email":       Access.DENY,
        "capture_calendar_poll":    Access.DENY,
        "create_calendar_event":    Access.DENY,
        "capture_inbox":            Access.DENY,
        "read_gmail_attachment":    Access.DENY,
        "capture_status":           Access.DENY,
    },

    # Réka NEM szerepel: a két lány nem lát bele egymás üzeneteibe.
    # (A beszélgetéseket amúgy is az `instance` mező particionálja.)
    visible_message_senders=[
        "AnnaKatheder",
        "kommandant",
        "system",
    ],

    allowed_recipients=[
        "kommandant",
        "web-claus",
        "cli-claus",
    ],

    readable_memory_categories=None,
    max_concurrent_ai_tasks=3,
    persona_system_prompt=KATHEDER_PROMPT,
)

#: Diák, sokat fogja használni — az első hét után nézd meg a tényleges
#: fogyást. Env-ből felülírható: `AN_DAILY_BUDGET_USD`.
ANNA_PROFILE.allowed_models = ["kimi", "kimi3", "deepseek", "glm5", "hy3"]
ANNA_PROFILE.daily_budget_usd = 5.0


def register_anna():
    register_instance(ANNA_PROFILE)
    return ANNA_PROFILE


if __name__ == "__main__":
    from permissions import check_permission, PermissionDeniedError
    p = register_anna()
    print(f"✅ {p.instance_id} ({p.display_name})")
    for tool in ("read_memory", "ai_query", "search_web", "scrape_url"):
        print(f"  ✓ {tool}: {check_permission('AnnaKatheder', tool).value}")
    for tool in ("capture_gmail_poll", "brave_navigate", "capture_calendar_poll"):
        try:
            check_permission("AnnaKatheder", tool)
            print(f"  ❌ {tool}: ÁTMENT — ez hiba!")
        except PermissionDeniedError:
            print(f"  🚫 {tool}: DENY")
