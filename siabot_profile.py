"""
SIaBot Permission Profile
=========================
A SIaBot (Self Improving Agent Bot Bridge) desktop-héj Bridge-hozzáférése.

MIÉRT KELL KÜLÖN PROFIL — a mai állapot és a baj vele
------------------------------------------------------
A SIaBot 2026-08-29-én a `km-` (Kommandant) tokennel kapcsolódott, mert az
volt kéznél. A `km-` a legnagyobb jogú token: benne van az `oversight_open`,
a `family_chat` és a `family_chat_kill` — vagyis rálátás a lányok
beszélgetéseire. Egy desktop-appnak, amiben AUTONÓM BOTOK futnak, ez több,
mint amennyi kell, és pontosan az az „implicit bizalmi sodródás", amit a
heimkehr-terv csapdaként sorol.

Ez a profil a szűkítés. A SIaBot dolgozzon — a családhoz semmi köze.

HATÁSKÖRI ELV
-------------
„Alles was nicht erlaubt ist, ist verboten." A `can_access` alapértéke DENY,
tehát ami itt nincs felsorolva, az tiltva van. Egy holnap felvett Bridge-tool
a SIaBot számára NEM jelenik meg magától — ez a helyes bukás-irány, és
ugyanaz a szabály, mint a héj oldalán a READ_ONLY_TOOLS allow-listája.

⚠️⚠️ ÁLLAPOT 2026-08-29 ESTE: a `siabot` IDEIGLENESEN BEKERÜLT a
`CORE_INSTANCES`-be (`permissions.py`), mert a héjnak recepteket kell tudnia
létrehozni, és a Bridge különben nem fogadja el a hívóját. EMIATT EZ A PROFIL
MA NEM HAT — alszik. A piacra lépés napján a `CORE_INSTANCES`-ből törlendő a
`siabot`, és EZ a fájl importálandó a `server.py`-ban; a tesztek addig is
őrzik, hogy a tartalma helyes maradjon.

⚠️ A SIaBot SZÁNDÉKOSAN NEM LENNE CORE INSTANCE. A `CORE_INSTANCES` tagjai
(web-claus, cli-claus, kommandant, feldwebel) megkerülik a szűrőket; ha a
SIaBot közéjük kerülne, ez az egész fájl dísz lenne.
"""

from permissions import (
    Access,
    PermissionProfile,
    register_instance,
)

INSTANCE_ID = "siabot"

# A megtagadott felületek, kimondva — hogy a lista OLVASHATÓ legyen, ne csak
# a hiányából lehessen kikövetkeztetni. Ezek nem szerepelnek a
# tool_permissions-ben, tehát a DENY alapérték fogja meg őket; ez a konstans
# a dokumentáció és a teszt közös horgonya.
DENIED_FAMILY_SURFACES = (
    "family_chat",       # a lányok beszélgetésének nyers szövege
    "family_chat_kill",  # vendég-munkamenet kilövése
    "family_presence",   # ki mikor használja az asszisztensét
    "oversight_open",    # rálátási ablak nyitása
)

SIABOT_PROFILE = PermissionProfile(
    instance_id=INSTANCE_ID,
    display_name="SIaBot",
    description=(
        "A SIaBot desktop-héj és a benne futó botok. Munka-hozzáférés: "
        "memória, feladatok, statisztika, archívum, capture. A családi "
        "felületekhez nincs jogosultsága."
    ),

    tool_permissions={
        # --- MEMÓRIA -------------------------------------------------
        # Írás is: a botok munkája memóriába kerül. A `write_memory` a héj
        # oldalán jóváhagyó kártyát húz (nincs a READ_ONLY_TOOLS-ban), tehát
        # az „ALLOW" itt azt jelenti, hogy a Bridge nem tiltja — nem azt,
        # hogy kérdés nélkül megtörténik. A két kapu sorban áll, nem
        # egymás helyett.
        "list_memory": Access.ALLOW,
        "read_memory": Access.ALLOW,
        "search_memory": Access.ALLOW,
        "write_memory": Access.ALLOW,

        # --- ÜZENETEK ------------------------------------------------
        "send_message": Access.ALLOW,
        "read_messages": Access.FILTERED,
        "read_new": Access.FILTERED,
        "search_messages": Access.FILTERED,
        "mark_read": Access.ALLOW,
        "thread_summary": Access.ALLOW,

        # --- FELADATOK ÉS MEGBESZÉLÉSEK ------------------------------
        "create_task": Access.ALLOW,
        "update_task": Access.ALLOW,
        "list_tasks": Access.ALLOW,
        "list_discussions": Access.ALLOW,
        "start_discussion": Access.ALLOW,
        "add_to_discussion": Access.ALLOW,
        "read_discussion": Access.ALLOW,
        "resolve_discussion": Access.ALLOW,
        "search_discussions": Access.ALLOW,

        # --- STATISZTIKA (StatData) ----------------------------------
        # Mind lekérdezés; ez a „berendezett város" gazdasági fele.
        "statdata_search": Access.ALLOW,
        "statdata_eurostat": Access.ALLOW,
        "statdata_ksh_hvd": Access.ALLOW,
        "statdata_ksh_stadat": Access.ALLOW,
        "statdata_dbnomics_search": Access.ALLOW,
        "statdata_dbnomics_series": Access.ALLOW,
        "statdata_yfinance": Access.ALLOW,
        "statdata_calculate": Access.ALLOW,
        "statdata_mnb_rates": Access.ALLOW,
        "statdata_recipe_book": Access.ALLOW,
        "statdata_fred": Access.ALLOW,
        "statdata_forecast": Access.ALLOW,
        "statdata_economic_calendar": Access.ALLOW,
        "statdata_policy_rates": Access.ALLOW,
        "statdata_ecb": Access.ALLOW,
        "statdata_help": Access.ALLOW,
        "statdata_macro": Access.ALLOW,
        "statdata_flash": Access.ALLOW,

        # --- ARCHÍVUM (Echolot) --------------------------------------
        "echolot_query": Access.ALLOW,
        "read_story_comments": Access.ALLOW,
        "read_agora_comments": Access.ALLOW,
        "agora_status": Access.ALLOW,

        # --- POSTAFIÓK ÉS NAPTÁR -------------------------------------
        # Az olvasó oldal. A KÜLDÉS (`capture_send_email`) és a naptárba
        # írás (`create_calendar_event`) szándékosan HIÁNYZIK: az
        # alkotmány szerint „nélküled nem küld", és egy Bridge-szintű
        # ALLOW itt azt jelentené, hogy a tiltás egyetlen helyen, a héj
        # allow-listáján múlik. Két kapu jobb, mint egy.
        "capture_inbox": Access.ALLOW,
        "capture_status": Access.ALLOW,
        "read_gmail_attachment": Access.ALLOW,
        "capture_gmail_poll": Access.ALLOW,
        "capture_calendar_poll": Access.ALLOW,

        # --- JÓVÁHAGYÁSI SOR -----------------------------------------
        # Olvasható. Az `action_draft_decide` NINCS itt: a függő jóváhagyás
        # eldöntése maga a jóváhagyás, és azt nem adhatja meg magának
        # egyetlen agent sem.
        "action_drafts_list": Access.ALLOW,
        "draft_reply_log": Access.ALLOW,

        # --- ÁLLAPOT --------------------------------------------------
        "get_status": Access.ALLOW,
        "heartbeat": Access.ALLOW,
        "log_session": Access.ALLOW,
    },

    # Kivel beszélhet. A lány-instance-ok NINCSENEK a listán.
    allowed_recipients=[
        "web-claus",
        "cli-claus",
        "kommandant",
    ],

    # Kinek az üzeneteit láthatja (a FILTERED szintekhez).
    visible_message_senders=[
        "siabot",
        "web-claus",
        "cli-claus",
        "kommandant",
    ],

    # ⚠️ PER-USER MEMÓRIA — a pitch 4. pontja („userenként ZÁRT emlékezet").
    # A mező a PermissionProfile-ban 2026-08-29-ig DEKLARÁLT, DE SEHOL NEM
    # HASZNÁLT volt: a memória-szűrés kategória szerint ment, nem gazda
    # szerint. A beállítása önmagában NEM izolál — az izolációt a
    # server.py memória-útvonalán kell érvényesíteni. Amíg az nincs kész,
    # ez a mező SZÁNDÉK, nem garancia, és a `test_siabot_profile.py`
    # ki is mondja, hogy melyik.
    memory_namespace=INSTANCE_ID,

    readable_memory_categories=None,
    max_concurrent_ai_tasks=3,
)


def register() -> PermissionProfile:
    """Beregisztrálja a SIaBot profilt a Bridge instance-regiszterébe."""
    register_instance(SIABOT_PROFILE)
    return SIABOT_PROFILE


register()
