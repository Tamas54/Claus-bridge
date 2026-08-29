"""
A SIaBot Bridge-profiljának invariánsai.

Ez a teszt NEM a memóriát méri, és nem nyúl a prodhoz — csak azt a
jogosultsági profilt vizsgálja, amit a `siabot_profile.py` regisztrál.
A profil a `km-` (Kommandant) token szűkítése: a SIaBot dolgozzon, a
családhoz semmi köze.

Amit itt rögzítünk, azt egy későbbi bővítés nem tudja csendben elrontani:
egy új tool felvétele a profilba, ami küld, költ vagy a lányokra lát,
pirosra váltja az első két esetet.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from permissions import (  # noqa: E402
    CORE_INSTANCES,
    Access,
    get_profile,
    is_core_instance,
)
from siabot_profile import (  # noqa: E402
    DENIED_FAMILY_SURFACES,
    INSTANCE_ID,
    SIABOT_PROFILE,
)


def test_a_profil_regisztralva_van():
    assert get_profile(INSTANCE_ID) is SIABOT_PROFILE


def test_a_core_tagsag_ideiglenes_es_tudatos():
    """A `siabot` MA core instance — és ez a teszt azért van, hogy ez soha ne
    legyen véletlen.

    A core tagok megkerülik a szűrőket, tehát amíg a `siabot` közöttük van,
    EZ A PROFIL NEM HAT. Kommandant-döntés 2026-08-29: fejlesztés alatt a
    teljes rálátás a munkaeszköz, és a héjnak recepteket is létre kell tudnia
    hozni. A szűkítést addig a HÉJ oldali allow-list és a jóváhagyó kártya
    végzi — egy kapu, nem kettő.

    ⏰ A PIACRA LÉPÉS NAPJÁN: vedd ki a `siabot`-ot a `CORE_INSTANCES`-ből,
    importáld a `siabot_profile`-t a `server.py`-ban, és fordítsd vissza ezt
    a tesztet `assert INSTANCE_ID not in CORE_INSTANCES`-re. A többi eset
    addig is őrzi, hogy a profil TARTALMA helyes maradjon, hogy azon a napon
    ne kelljen újraírni.
    """
    assert INSTANCE_ID in CORE_INSTANCES
    assert is_core_instance(INSTANCE_ID) is True


def test_a_csaladi_feluletek_tiltva_vannak():
    for tool in DENIED_FAMILY_SURFACES:
        assert SIABOT_PROFILE.can_access(tool) is Access.DENY, tool


def test_amit_nem_soroltunk_fel_az_tiltva_van():
    # "Alles was nicht erlaubt ist, ist verboten." Egy holnap felvett
    # Bridge-tool nem jelenhet meg a SIaBot előtt magától.
    assert SIABOT_PROFILE.can_access("valami_uj_tool_2027") is Access.DENY


def test_a_kuldes_es_a_dontes_nincs_megengedve():
    # Az alkotmány: "nélküled nem küld, nem költ, nem töröl" — és a függő
    # jóváhagyás eldöntése maga a jóváhagyás. A héj allow-listája ezeket
    # már kártyázza; ez itt a MÁSODIK kapu, hogy ne egyetlen helyen
    # múljon a tiltás.
    for tool in (
        "capture_send_email",
        "create_calendar_event",
        "action_draft_decide",
        "ai_query",
        "ai_task",
        "market_brief_now",
        "delete_recipe",
    ):
        assert SIABOT_PROFILE.can_access(tool) is Access.DENY, tool


def test_a_munkahoz_valo_olvasas_megvan():
    # A kapu másik fele: egy profil, ami semmit nem enged, nem biztonságos,
    # hanem használhatatlan.
    for tool in (
        "read_memory",
        "search_memory",
        "echolot_query",
        "statdata_macro",
        "capture_inbox",
        "list_tasks",
    ):
        assert SIABOT_PROFILE.can_access(tool) is Access.ALLOW, tool


def test_a_lanyok_nincsenek_a_cimzettek_kozott():
    for instance in ("YoungeReka", "Anna", "Bella"):
        assert SIABOT_PROFILE.can_send_to(instance) is False, instance


def test_a_namespace_szandek_es_nem_garancia():
    # A mező be van állítva, DE a `memory_namespace` a Bridge-ben 2026-08-29-én
    # deklarált-és-nem-használt: a memória-szűrés kategória szerint megy, nem
    # gazda szerint. Ez a teszt szándékosan CSAK a mező értékét rögzíti, és
    # nem állítja, hogy izolál — az izolációt a server.py memória-útvonalán
    # kell érvényesíteni, ADDITÍVAN (a namespace nélküli hívók dolga nem
    # változhat), és a prod adatbázis MÁSOLATÁN kell verifikálni.
    assert SIABOT_PROFILE.memory_namespace == INSTANCE_ID
