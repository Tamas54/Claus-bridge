"""
Bella Instance Profile
=======================
Saját felület, saját beszélgetések, saját keret.

JOGOSULTSÁGBAN AZONOS Rékáéval és Annáéval — ugyanaz a 18 tiltott tool
(email, naptár, minden `brave_*`). A tesztek ezt az azonosságot állítják
is: ha valaki bármelyik profilon lazít, pirosra vált.

AMIT SZÁNDÉKOSAN NEM KAPOTT
---------------------------
Semmilyen rálátást a lányok beszélgetéseire. Ez NEM technikai
mulasztás, hanem a biztonságos alapértelmezés: rálátást adni bármikor
lehet, visszavenni nem — és ha a lányok megtudják, hogy az anyjuk
beleláthat, onnantól azt fogják mérlegelni, mit írnak le. A rendszer
értéke azon áll, hogy őszintén írnak-e bele.

Ha a Kommandant másképp dönt, az egy sor: `visible_message_senders`
és egy külön oversight-tool. De ez az ő döntése, nem az enyém.
"""


from permissions import (
    PermissionProfile,
    Access,
    register_instance,
)
from youngereka_access import BELLA_CHAT_PROMPT

BELLA_PROFILE = PermissionProfile(
    instance_id="Bella",
    display_name="Bella",
    description="Saját felület — mínusz személyes email/naptár, "
                "böngésző-vezérlés, privát Claus-üzenetek és MÁSOK "
                "beszélgetései.",

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

    # A LÁNYOK NEM SZEREPELNEK. Bella nem lát bele a beszélgetéseikbe —
    # se üzenetben, se máshogy. Lásd a modul fejlécét: ez döntés, nem
    # mulasztás.
    visible_message_senders=[
        "Bella",
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
    persona_system_prompt=BELLA_CHAT_PROMPT,
)

#: Ugyanaz a napi keret, mint a többieknél.
BELLA_PROFILE.allowed_models = ["kimi", "kimi3", "deepseek", "glm5", "hy3"]
BELLA_PROFILE.daily_budget_usd = 5.0


def register_bella():
    register_instance(BELLA_PROFILE)
    return BELLA_PROFILE


if __name__ == "__main__":
    from permissions import check_permission, PermissionDeniedError
    p = register_bella()
    print(f"✅ {p.instance_id} ({p.display_name})")
    for tool in ("read_memory", "ai_query", "search_web", "scrape_url"):
        print(f"  ✓ {tool}: {check_permission('Bella', tool).value}")
    for tool in ("capture_gmail_poll", "brave_navigate", "capture_calendar_poll"):
        try:
            check_permission("Bella", tool)
            print(f"  ❌ {tool}: ÁTMENT — ez hiba!")
        except PermissionDeniedError:
            print(f"  🚫 {tool}: DENY")
