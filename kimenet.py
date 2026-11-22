"""Csatornafüggetlen kimenet (Bridge UI v2, 2026-11-22): az asszisztens egy beszélgetési köre oda szól, ahonnan kérdezték.

Miért: a Telegram-út (`server._handle_telegram_message` → Faber-szándék → Feldwebel / @ügynök) minden kimondott szavát
`_telegram_push`-sal küldte — a Feldwebel parancsai, a válasz, az API-hiba és a Faber-nyugta is. A webes felületnek
(`/app`) UGYANEZ az agy kell (egy asszisztens, két ablak), de a válasznak a webre kell visszajönnie. A 30+ push-hely
átírása helyett egy kontextusváltozó mondja meg, hova szól az éppen futó kör:

  - nincs aktív `Kimenet` (Telegram-kör, ütemező, háttérszál) → minden pontosan úgy megy, mint eddig, Telegramra;
  - aktív, nyitott `Kimenet` (webes kör) → a `_telegram_push` szövege a kör tételei közé kerül, Telegramra NEM megy.

A kör végén a `Kimenet` lezárul (`nyitva = False`). Ha egy körből indított háttérfeladat később szólal meg (pl. egy
hosszú recept eredménye), az már a lezárt kimenetet látja, és Telegramra megy — nem vész el egy lezárt webes körben.
Szál (`threading.Thread`) nem örököl kontextust, az mindig Telegram.

`nyom`: a kör élő nyoma a felületnek — melyik modell válaszolt valójában (`served_by`-doktrína: a kapu mögött a kért
SiliconFlow-modell helyett Anthropic is felelhet), és milyen eszközöket hívott az asszisztens, futás közben is.
"""
from __future__ import annotations

import contextvars
import json
import time
from datetime import datetime, timezone


class Kimenet:
    """Egy beszélgetési kör kimenete. A `tetelek` a kimondott szövegek (Telegram-HTML, ahogy a push kapta), időrendben."""

    def __init__(self, csatorna: str = "web"):
        self.csatorna = csatorna
        self.tetelek: list[dict] = []
        self.nyom: dict = {"modell": None, "szolgaltato": None, "eszkozok": [], "faber": None, "ugynok": None}
        self.nyitva = True


AKTUALIS: contextvars.ContextVar = contextvars.ContextVar("bridge_kimenet", default=None)


def _most() -> str:
    return datetime.now(timezone.utc).isoformat()


def elkap(szoveg: str) -> bool:
    """A `_telegram_push` első lépése: ha webes kör fut, a szöveg az övé (True), különben megy Telegramra (False)."""
    k = AKTUALIS.get()
    if k is None or not k.nyitva:
        return False
    k.tetelek.append({"ido": _most(), "html": szoveg})
    return True


def nyom() -> dict | None:
    """Az éppen futó webes kör nyoma, vagy None (Telegram-kör, vagy már lezárt kör)."""
    k = AKTUALIS.get()
    return k.nyom if k is not None and k.nyitva else None


def modell(data: dict | None, ugynok: str | None = None) -> None:
    """A válasz VALÓBAN kiszolgáló modellje (az LLM-válasz `model` mezője) a nyomba."""
    n = nyom()
    if n is None or not isinstance(data, dict):
        return
    if data.get("model"):
        n["modell"] = data.get("model")
        n["szolgaltato"] = data.get("provider") or "siliconflow"
    if ugynok:
        n["ugynok"] = ugynok


def eszkoz_indul(nev: str, args) -> dict | None:
    """Egy eszközhívás kezdete a nyomban (a felület „fut” állapotban mutatja). A visszaadott tételt az `eszkoz_kesz` zárja."""
    n = nyom()
    if n is None:
        return None
    try:
        args_szoveg = json.dumps(args, ensure_ascii=False)[:600]
    except (TypeError, ValueError):
        args_szoveg = str(args)[:600]
    tetel = {"nev": str(nev), "args": args_szoveg, "allapot": "fut", "eredmeny": "", "ms": None, "_t0": time.monotonic()}
    n["eszkozok"].append(tetel)
    return tetel


def eszkoz_kesz(tetel: dict | None, eredmeny) -> None:
    if tetel is None:
        return
    szoveg = eredmeny if isinstance(eredmeny, str) else str(eredmeny)
    tetel["eredmeny"] = szoveg[:1200]
    tetel["ms"] = int((time.monotonic() - tetel.pop("_t0", time.monotonic())) * 1000)
    low = szoveg[:200].lower()
    tetel["allapot"] = "hiba" if (low.startswith('{"error"') or low.startswith("error")) else "kesz"
