#!/usr/bin/env python3
"""NACHTSCHICHT PRÓBAFUTÁS a Kommandant gépén (2026-11-22) — ugyanaz a `nachtschicht.ejszaka`, mint a Railway-en, de:

  * a Bridge-adat (Telegram/web-üzenetek, feladatok) ÉLŐBEN, a Bridge REST-jéről, munkáskulccsal (csak olvas);
  * a Gmail és a naptár egy BEMENETI JSON-ból (a Google-token csak a Railway-en van): {"levelek": […], "naptar": […]}
    a `BridgeForras` alakjában (a levél: id, threadId, from, from_email, date, subject, snippet, body);
  * a `tudasbazis` a gépen helyben (a futár ~/faber/futar.py `vegrehajt`-jával, ugyanaz a parancs);
  * a Lexonomics / Medical / Makronóm és a Studio ÉLŐBEN (kulso_mcp, a kulcsok env-ben);
  * a motor ÉLŐBEN: `llm_kapu.chat` az Anthropic-kulccsal (env-ben);
  * Gmail-tervezet NEM készül (nincs Google-token): a szövege a jelentésbe kerül; Telegram NEM megy; Faber-kérelem NEM
    megy (csak kiírja, mit kérne); a napló egy ideiglenes adatbázisba ír (a `--db`).

Futtatás (a titkok env-ben, kiírás nélkül):
  ANTHROPIC_API_KEY=… STUDIO_MCP_KEY=… LEXONOMICS_MCP_KEY=… BRIDGE_MUNKAS_KULCS=… \\
    .venv/bin/python scripts/nachtschicht_proba.py --bemenet bemenet.json --db /tmp/…/proba.db [--max-tetel 2]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

GYOKER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, GYOKER)

import nachtschicht as N  # noqa: E402

BRIDGE = os.environ.get("BRIDGE_URL", "https://web-production-d194b.up.railway.app")
FABER = os.path.expanduser("~/faber")


def _db(ut: str):
    c = sqlite3.connect(ut)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
          sender TEXT NOT NULL, recipient TEXT NOT NULL, subject TEXT NOT NULL, message TEXT NOT NULL,
          priority TEXT DEFAULT 'normal', thread_id INTEGER, reply_to INTEGER, status TEXT DEFAULT 'unread');
        CREATE TABLE IF NOT EXISTS shared_memory (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE,
          value TEXT NOT NULL, category TEXT DEFAULT 'general', tags TEXT DEFAULT '', updated_by TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    """)
    c.commit()
    c.close()

    def get_db():
        k = sqlite3.connect(ut)
        k.row_factory = sqlite3.Row
        return k
    return get_db


def _memoria_ir(*a, **k):
    raise RuntimeError("próbafutásban a shared_memory-be nem írunk")


class ProbaForras(N.BridgeForras):
    def __init__(self, bemenet: dict, kulcs: str):
        from feldwebel import BridgeContext
        super().__init__(BridgeContext(), None)
        self.bemenet, self.kulcs = bemenet, kulcs
        self.tervezetek = []

    async def _rest(self, ut: str):
        import httpx
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BRIDGE}{ut}", headers={"X-Bridge-Key": self.kulcs})
        r.raise_for_status()
        return r.json()

    async def uzenetek(self, ota):
        sorok = await self._rest("/api/messages?limit=400")
        return [r for r in sorted(sorok, key=lambda r: r["id"]) if N._beszelgetes_sor(r, ota.isoformat())]

    async def feladatok(self):
        return [r for r in await self._rest("/api/tasks") if r.get("status") in ("pending", "in_progress")][:30]

    async def teendok(self):
        return []

    async def naptar(self, tol, ig):
        return [e for e in self.bemenet.get("naptar") or []
                if tol <= (N._datum(e.get("start")) or tol) <= ig]

    async def levelek(self, query, limit):
        levelek = sorted(self.bemenet.get("levelek") or [], key=lambda l: l.get("date") or "", reverse=True)
        if query in (N.LEVEL_FRISS, N.LEVEL_HATARIDO):
            return [{k: v for k, v in l.items() if k != "body"} for l in levelek][:limit]
        szavak = [s.lower() for s in re.findall(r"[^\s()]+", query) if ":" not in s and s not in ("OR", "AND")]
        cimek = [s.split(":", 1)[1].lower() for s in query.split() if s.startswith(("from:", "to:"))]
        talalat = [l for l in levelek if (not szavak or any(s in json.dumps(l, ensure_ascii=False).lower()
                                                            for s in szavak))
                   and (not cimek or any(c in str(l.get("from_email")).lower() for c in cimek))]
        return [{k: v for k, v in l.items() if k != "body"} for l in talalat][:limit]

    async def level(self, mid):
        for l in self.bemenet.get("levelek") or []:
            if l.get("id") == mid:
                return {**{k: l.get(k) for k in ("id", "threadId", "from", "subject", "date", "labels")},
                        "body": l.get("body") or l.get("snippet") or ""}
        raise KeyError(f"nincs ilyen levél a bemenetben: {mid}")

    async def tervezet(self, *, message_id, to, subject, body):
        eredeti = next((l for l in self.bemenet.get("levelek") or [] if l.get("id") == message_id), {})
        to = to or eredeti.get("from_email") or ""
        subject = subject or (lambda s: s if s.lower().startswith("re:") else f"Re: {s}")(eredeti.get("subject") or "")
        t = {"draft_id": None, "to": to, "subject": subject, "thread_id": eredeti.get("threadId"), "link": None,
             "proba": True, "body": body}
        self.tervezetek.append(t)
        return t

    def tudasbazis_elerheto(self):
        return os.path.exists(os.path.join(FABER, "futar.py"))

    async def feldwebel_eszkoz(self, nev, args, modell):
        if nev == "tudasbazis":
            import futar_kliens
            if not futar_kliens.claude_modell(modell):
                return json.dumps({"error": futar_kliens.CSAK_CLAUDE}, ensure_ascii=False)
            param, hiba = futar_kliens._param("tudasbazis", args)
            if hiba:
                return json.dumps({"error": hiba}, ensure_ascii=False)
            if FABER not in sys.path:
                sys.path.insert(0, FABER)
            import futar
            v = await asyncio.to_thread(futar.vegrehajt, "tudasbazis", param)
            if not v["ok"]:
                return json.dumps({"error": v["hiba"]}, ensure_ascii=False)
            return json.dumps({"forras": "a Kommandant tudásbázisa (a gépén)", "valasz": v["szoveg"][:12000],
                               "mp": v["mp"]}, ensure_ascii=False)
        if nev == "gmail_search":
            return json.dumps({"messages": await self.levelek(str(args.get("query") or ""), 10)}, ensure_ascii=False)
        if nev == "read_gmail_attachment":
            return json.dumps({"error": "próbafutás: a melléklet itt nem olvasható (a Google-token a Railway-en van)"},
                              ensure_ascii=False)
        return await super().feldwebel_eszkoz(nev, args, modell)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bemenet", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--max-tetel", type=int, default=2)
    a = ap.parse_args()
    for v in ("ANTHROPIC_API_KEY", "BRIDGE_MUNKAS_KULCS"):
        if not os.environ.get(v):
            raise SystemExit(f"hiányzik a környezetből: {v}")
    forras = ProbaForras(json.load(open(a.bemenet, encoding="utf-8")), os.environ["BRIDGE_MUNKAS_KULCS"])
    get_db = _db(a.db)
    t0 = datetime.now(timezone.utc)
    r = await N.ejszaka(forras=forras, get_db=get_db, memoria_ir=_memoria_ir, proba=True, max_tetel=a.max_tetel)
    print("=" * 100)
    print(r["jelentes"])
    print("=" * 100)
    print(f"költség: ${r['koltes_usd']:.4f} · {r['hivasok']} kapuhívás · {(datetime.now(timezone.utc) - t0).seconds} mp")
    print(f"erős jelek: {[j['id'] + ' ' + j['cim'][:50] for j in r['jelek'] if j['erosseg'] == 'eros']}")
    for e in r["eredmenyek"]:
        print(f"\n--- tétel: {e.get('cim')} [{e.get('jel_id')}] {e.get('allapot')} · eszközök: {e.get('eszkozok')}"
              f" · elutasítva: {e.get('elutasitva')}")
        for t in e.get("tervezetek") or []:
            print(f"LEVÉLTERVEZET → {t['to']} · {t['subject']}\n{t['body']}")
    for p in r["panaszok"]:
        print(f"\n--- panasz ({p['allapot']}): {p.get('kivansag_teljes') or p.get('kivansag')}")
    c = get_db()
    print("\nnapló:", [(x["sender"], x["recipient"], x["status"], x["subject"]) for x in
                      c.execute("SELECT sender, recipient, status, subject FROM messages ORDER BY id")])
    c.close()


if __name__ == "__main__":
    asyncio.run(main())
