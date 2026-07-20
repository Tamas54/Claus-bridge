# ORAKEL-II BACKTESZT-GÉP

A kérdés: **a 2026-os OGY választás ELŐTTI hírképből kiolvasható volt-e a tényleges eredmény** (Tisza 53,18% vs Fidesz 38,61%, rés +14,6)?
Ha igen — működő közvélemény-NOWCASTER helyett **valódi, vakon igazolt választási előrejelző**.

## A három fogaskerék

1. **`config.py`** — a 9 lap, KAMPÁNY-IDŐSZAKI leannel + média-elérési súlyok + poll-szűrő szólista + időablak (jan 1.–ápr. 1.).
2. **`collect_corpus.py`** — vak, forrás-vezérelt, lean-arányos, poll-szűrt korpusz-gyűjtő → `corpus.jsonl` (~500 cím+lead).
3. **`run_panel.py`** — szintetikus választói panel (demográfia + média-környezet + korpusz) → `panel_results.json` + verdikt.

## A NÉGY SZABÁLY (mind vak a végeredményre)

- **Forrás-vezérelt** — a lap domainjére keresünk, nem témákra (nincs emberi botrány-válogatás → nincs hindsight bias).
- **Időben egyenletes** — jan/feb/márc arányosan.
- **Lean-arányos** — a reach-súly szerinti kvóta, nem fele-fele.
- **Poll-szűrő** — konkrét pártszámot tartalmazó cikk KIZÁRVA (a panel nem láthatja a kész választ).

## Futtatás

```bash
# 1. Korpusz-gyűjtés (Brave Search API kell)
export BRAVE_API_KEY="..."
python3 collect_corpus.py        # -> corpus.jsonl

# 2. Panel-futtatás (Anthropic API kell)
export ANTHROPIC_API_KEY="..."
python3 run_panel.py             # -> panel_results.json + verdikt
```

## A verdikt olvasata

A panel a `decided` körön belüli **Tisza–Fidesz rést** adja, a hivatalos +14,6 ellen:
- **±6 pont** → TALÁLAT. A médiakörnyezetből kiolvasható volt. Doktrína igazolva.
- **±12 pont** → KÖZELÍT. Helyes irány, finomítható.
- **Tisza-előny, de >+12 eltérés** → irány jó, magnitúdó nem.
- **negatív rés** → prior-fal, a média-grounding nem fogott.

## Hangolható pontok (Kommandant kezében)

- `config.py SOURCES` — a 9 lap, a kampány-leanek, a `reach_weight` súlyok (NMHH-adattal pontosítható).
- `config.py STRUCTURAL_MEDIA` — a közmédia=gov flip és a Tisza social-fölény. **Ez a kulcs-paraméter.**
- `run_panel.py` demográfia-cellák — KSH-arányok finomíthatók.
- `PANEL_MODEL` — a #3-ban a gpt-5.4-mini volt a befutó (0,5 szórás); a Sonnet az alapért. Provider-váltó beépíthető.

## A TISZTASÁG VÉDELME

A gép azért épült így, hogy **kivegye a kezedet a válogatásból**. Te tudod az eredményt — a gép nem.
Ne nyúlj a korpusz-válogatásba az eredmény ismeretében (az visszafelé-illesztés lenne).
A `STRUCTURAL_MEDIA` flip az egyetlen tudatos beavatkozás, és az is a *választás előtti* médiavalóságot rögzíti, nem a kimenetet.

## Egy futás = egy jel

Több seed, több nap, ismételhetőség kell a bizonyításhoz. Egy találat lehet szerencse;
a recept akkor áll meg, ha **reprodukálható**.
