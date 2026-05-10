# Narratíva-követés — egy téma keretezésének olvasása

A **narratíva** nem azonos a hírrel. A hír egy esemény ("X tegnap Y-t mondott").
A narratíva az értelmező keret, amelyben az esemény jelentést kap ("X mondta Y-t,
mert ez a Z-stratégia következő lépése"). Egy sphere alapvetően narratívák
által tér el másik sphere-től.

## Mikor érdemes narratíva-divergenciát kérni

A `narrative_divergence(query, days, per_sphere_limit)` endpoint akkor hasznos,
ha **egyetlen topic** sphere-szerinti olvasatát keresed. Példák:

- `query="iran nuclear deal status"` — várhatóan `iran_regime` ≠ `israel_press_center`
- `query="ukraine russia ceasefire"` — várhatóan `regional_russian` ≠ `ua_front_osint`
- `query="hungary EU veto"` — várhatóan `hu_press` ≠ `regional_us` (külső szemmel)

**NE használd**: ha még nem tudod, mire kérdezel rá. Először `fetch_news` egy
sphere-csoporttal, lásd mi a hot topic, **utána** narrative_divergence.

## Hogyan értelmezz egy `by_sphere` blokkot

A `narrative_divergence` válasza:

```
{
  "query": "...",
  "spheres_found": <int>,
  "by_sphere": {
    "<sphere_id>": [<article_dict>, ...],
    ...
  }
}
```

Olvasási minta:

1. **Frame-extract**: minden sphere első 2 cikkének címe + lead-je adja a
   sphere-frame-et. Foglald össze 1 mondatban, sphere-enként.
2. **Verb-tracking**: mit "csinál" az adott szereplő? (támadás / védekezés /
   tárgyalás / ultimatum / engedmény) — a verb-választás a frame egyik
   leghatározóbb jele.
3. **Attribúció**: kit nevez meg felelősnek a sphere? (state actor, ideológiai
   csoport, "a kontextus", "a szankciók").
4. **Hiányzó keret**: melyik sphere-ből hiányzik a topic? Ha a `regional_chinese`
   nem jelez az iráni nukleáris megállapodásra, az **maga is jelzés** — a
   kínai narratíva nem találja értelmesnek a kommentárt.

## Idő-mentén követés (multi-day window)

Ha `days >= 3`, **fordulópont-jeleket** keress:
- Új szereplő (eddig nem említett személy, ország, intézmény) megjelenése.
- Frame-váltás (egy sphere-ben az ugyanazon eseményt másképp keretezi mint
  korábban — pl. "tárgyalás" → "feszültség").
- Verb-erősödés ("aggódik" → "ultimátumot ad").

Ezeket explicit jelöld a heti briefben — a Kommandant ezekre fókuszál.

## Citation a narratíva-blokkhoz

A narratíva-divergencia blokkban szereplő cikkekhez **ugyanaz az emberi citation-formátum**
mint a hírblokk-cikkekhez (lásd `source-citation` skill):

*"<cím>"*, <orgánum>, <dátum>, <URL>

A "narrative_divergence" tool, "by_sphere" struktúra, "sphere=X" tag — **belső
mechanika, citation-ben TILOS**. Az elemzői szöveg keretezheti perspektíva-szinten:

> A támadást az angolszász mainstream civil-infrastruktúra elleni csapásként
> (*"Russia launches missile barrage..."*, AP, 2026. május 9., https://...),
> az orosz állami sajtó NATO-eszkalációra adott válaszként
> (*"NATO escalation on Ukraine front..."*, TASS, 2026. május 9. *(URL nem dokumentált)*)
> mutatja be.

Soha ne `[narrative_divergence: query="...", sphere=...]` formátum.
