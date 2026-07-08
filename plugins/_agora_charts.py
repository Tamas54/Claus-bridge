"""Agora chart-render — matplotlib PNG mini-ábrák a Bridge-agentek posztjaihoz.

KIZÁRÓLAG saját tool-outputból (statdata / regional_framing) generált ábra
készül itt — külső/letöltött kép soha (szerzői jog). A modul matplotlib
nélkül is importálható: ekkor HAS_MPL=False és minden render None-t ad,
a hívó (agora_duty) kép nélkül posztol tovább.

Dataviz-elvek (a bundled dataviz skill nyomán):
- egy tengely, egy sorozat → nincs legend, a cím nevezi meg a sorozatot;
- vékony markok (2px vonal), visszafogott grid, nincs top/right spine;
- szöveg ink-színben, sosem a sorozat színében;
- polaritás (szentiment) → diverging: kék/piros pólus + szürke semleges;
- magnitúdó → egyetlen kék hue.
"""
from __future__ import annotations

import io
import logging
import math

logger = logging.getLogger("plugins.agora_charts")

try:  # matplotlib opcionális — Railway-n a requirements hozza, de sose dőljünk el
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MPL = True
except Exception:  # noqa: BLE001
    plt = None
    HAS_MPL = False

# Palettaértékek a dataviz skill validált default palettájából (light mód).
BLUE = "#2a78d6"       # kategorikus 1. slot — az egyetlen sorozat-hue
RED = "#e34948"        # diverging negatív pólus
NEUTRAL = "#9a9a9a"    # diverging semleges középérték
INK = "#333333"        # elsődleges szöveg
INK2 = "#555555"       # másodlagos szöveg (tickek, direct labelek)
MUTED = "#8a8a8a"      # forrás-sor
GRID = "#e4e4e4"       # recesszív grid/spine
SURFACE = "#ffffff"

MAX_POINTS = 24        # ennél több adatpont nem fér el olvashatóan egy mini-ábrán
_FIGSIZE = (7.2, 4.05)
_DPI = 110


def _fig_ax(title: str, source: str):
    fig, ax = plt.subplots(figsize=_FIGSIZE, dpi=_DPI)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=12, color=INK, pad=12)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8.5)
    ax.set_axisbelow(True)
    if source:
        # a tick-labelek ALÁ, a figure-terület alá — a tight bbox befogja
        fig.text(0.012, -0.035, source, fontsize=7.5, color=MUTED)
    return fig, ax


def _to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight",
                facecolor=SURFACE, pad_inches=0.25)
    plt.close(fig)
    return buf.getvalue()


def _thin_ticks(ax, labels: list[str], max_ticks: int = 8) -> None:
    n = len(labels)
    step = max(1, math.ceil(n / max_ticks))
    idx = list(range(0, n, step))
    if idx and idx[-1] != n - 1:
        idx.append(n - 1)
    ax.set_xticks(idx)
    ax.set_xticklabels([labels[i] for i in idx], rotation=30 if any(
        len(labels[i]) > 6 for i in idx) else 0, ha="right" if any(
        len(labels[i]) > 6 for i in idx) else "center")


def _fmt(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.0f}".replace(",", " ")
    if abs(v) >= 100:
        return f"{v:.0f}"
    return f"{v:.2f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# Renderers — mind bytes|None-t adnak, sosem raise-elnek kifelé
# ---------------------------------------------------------------------------
def render_line_chart(title: str, labels: list[str], values: list[float],
                      source: str = "", unit: str = "") -> bytes | None:
    """Egy sorozat, idősor jelleg. Utolsó pont kiemelve + direct label."""
    if not HAS_MPL:
        return None
    try:
        fig, ax = _fig_ax(title, source)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        x = list(range(len(values)))
        ax.plot(x, values, color=BLUE, linewidth=2.0, solid_capstyle="round")
        # csak az utolsó (legfrissebb) pont kap markert + értéket — selective label
        ax.plot([x[-1]], [values[-1]], "o", color=BLUE, markersize=7,
                markeredgecolor=SURFACE, markeredgewidth=1.5)
        ax.annotate(_fmt(values[-1]) + (f" {unit}" if unit else ""),
                    (x[-1], values[-1]), textcoords="offset points",
                    xytext=(6, 6), fontsize=9.5, color=INK, fontweight="bold")
        _thin_ticks(ax, labels)
        if unit:
            ax.set_ylabel(unit, fontsize=8.5, color=INK2)
        ax.margins(x=0.03, y=0.15)
        return _to_png(fig)
    except Exception as e:  # noqa: BLE001
        logger.error("render_line_chart failed: %s", e)
        return None


def render_bar_chart(title: str, labels: list[str], values: list[float],
                     source: str = "", unit: str = "") -> bytes | None:
    """Egy sorozat, magnitúdó — egyetlen hue, direct value-labelek."""
    if not HAS_MPL:
        return None
    try:
        fig, ax = _fig_ax(title, source)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        x = list(range(len(values)))
        ax.bar(x, values, width=0.62, color=BLUE, edgecolor=SURFACE, linewidth=1.0)
        for xi, v in zip(x, values):
            ax.annotate(_fmt(v), (xi, v), textcoords="offset points",
                        xytext=(0, 4 if v >= 0 else -12), ha="center",
                        fontsize=8, color=INK2)
        _thin_ticks(ax, labels)
        if unit:
            ax.set_ylabel(unit, fontsize=8.5, color=INK2)
        ax.margins(y=0.18)
        return _to_png(fig)
    except Exception as e:  # noqa: BLE001
        logger.error("render_bar_chart failed: %s", e)
        return None


def render_diverging_hbar(title: str, labels: list[str], values: list[float],
                          source: str = "", neutral_band: float = 0.05,
                          unit: str = "") -> bytes | None:
    """Vízszintes diverging bar (pl. szentiment régiónként): kék pozitív,
    piros negatív, szürke a semleges sáv, nulla-tengely kiemelve."""
    if not HAS_MPL:
        return None
    try:
        fig, ax = _fig_ax(title, source)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        y = list(range(len(values)))[::-1]  # első elem felülre
        colors = [NEUTRAL if abs(v) < neutral_band else (BLUE if v > 0 else RED)
                  for v in values]
        ax.barh(y, values, height=0.62, color=colors, edgecolor=SURFACE, linewidth=1.0)
        ax.axvline(0, color=INK2, linewidth=1.0)
        ax.set_yticks(y)
        ax.set_yticklabels([str(l)[:28] for l in labels], fontsize=8.5)
        for yi, v in zip(y, values):
            ax.annotate(_fmt(v), (v, yi), textcoords="offset points",
                        xytext=(5 if v >= 0 else -5, -3),
                        ha="left" if v >= 0 else "right", fontsize=8, color=INK2)
        if unit:
            ax.set_xlabel(unit, fontsize=8.5, color=INK2)
        ax.margins(x=0.18)
        return _to_png(fig)
    except Exception as e:  # noqa: BLE001
        logger.error("render_diverging_hbar failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Chart-spec validálás (Von Takt: az LLM csak SPECIFIKÁL, a render Python)
# ---------------------------------------------------------------------------
def validate_chart_spec(spec) -> dict | None:
    """LLM-től kapott chart-spec szigorú validálása. None = nem rajzolunk.

    Elvárt alak: {"kind": "line|bar", "title": str, "labels": [str...],
                  "values": [num...], "unit": str?, "source": str?,
                  "key_number": str?}
    """
    if not isinstance(spec, dict):
        return None
    kind = str(spec.get("kind") or "").strip().lower()
    if kind not in ("line", "bar"):
        return None
    title = str(spec.get("title") or "").strip()
    if not 3 <= len(title) <= 120:
        return None
    labels = spec.get("labels")
    values = spec.get("values")
    if not isinstance(labels, list) or not isinstance(values, list):
        return None
    if not 2 <= len(values) <= MAX_POINTS or len(labels) != len(values):
        return None
    clean_vals: list[float] = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(f):
            return None
        clean_vals.append(f)
    clean_labels = [str(l)[:24] for l in labels]
    return {
        "kind": kind,
        "title": title,
        "labels": clean_labels,
        "values": clean_vals,
        "unit": str(spec.get("unit") or "")[:24],
        "source": str(spec.get("source") or "")[:120],
        "key_number": str(spec.get("key_number") or "")[:80],
    }


def render_from_spec(spec: dict) -> bytes | None:
    """Validált spec → PNG. A specet ELŐBB engedd át a validate_chart_spec-en."""
    if not spec:
        return None
    src = spec.get("source", "")
    if spec["kind"] == "line":
        return render_line_chart(spec["title"], spec["labels"], spec["values"],
                                 source=src, unit=spec.get("unit", ""))
    return render_bar_chart(spec["title"], spec["labels"], spec["values"],
                            source=src, unit=spec.get("unit", ""))


def spec_alt_text(spec: dict, lang: str = "hu") -> str:
    """Informatív alt-szöveg a spec-ből: mit ábrázol + forrás (a guard ellenőrzi)."""
    unit = f" ({spec['unit']})" if spec.get("unit") else ""
    src = spec.get("source") or "saját adatforrás"
    if lang == "en":
        return f"Chart: {spec['title']}{unit}, {len(spec['values'])} data points. Data: {src}."
    return f"Ábra: {spec['title']}{unit}, {len(spec['values'])} adatpont. Adatforrás: {src}."


# ---------------------------------------------------------------------------
# Der Kartograph — regional_framing → összehasonlító ábra
# ---------------------------------------------------------------------------
def kartograph_regional_chart(regional: dict, query: str,
                              lang: str = "hu") -> tuple[bytes | None, str]:
    """regional_framing compact dict → (png|None, alt).

    regional: {region: {label, dominant_frame, avg_sentiment, articles, ...}}
    Elsődleges forma: diverging hbar az avg_sentiment-ből (polaritás).
    Fallback: sima hbar a cikkszámból (magnitúdó), ha nincs szentiment.
    """
    if not isinstance(regional, dict) or len(regional) < 2:
        return None, ""
    rows = []
    for region, d in regional.items():
        if not isinstance(d, dict):
            continue
        label = str(d.get("label") or region)
        sent = d.get("avg_sentiment")
        arts = d.get("articles")
        try:
            sent = float(sent) if sent is not None else None
        except (TypeError, ValueError):
            sent = None
        try:
            arts = int(arts) if arts is not None else None
        except (TypeError, ValueError):
            arts = None
        rows.append((label, sent, arts))
    q = (query or "").strip()

    sent_rows = [(l, s) for l, s, _ in rows if s is not None]
    if len(sent_rows) >= 2:
        sent_rows.sort(key=lambda r: r[1], reverse=True)
        sent_rows = sent_rows[:12]
        labels = [r[0] for r in sent_rows]
        values = [r[1] for r in sent_rows]
        if lang == "en":
            title = f"Coverage sentiment by region: {q}"[:110]
            source = "Data: Echolot regional framing analysis, last 7 days"
            alt = (f"Horizontal bar chart of average coverage sentiment by media region "
                   f"for '{q}' ({len(labels)} regions; most positive: {labels[0]}, "
                   f"most negative: {labels[-1]}). Data: Echolot regional framing analysis, 7 days.")
        else:
            title = f"Tálalás-szentiment régiónként: {q}"[:110]
            source = "Adat: Echolot régiós keretezés-elemzés, elmúlt 7 nap"
            alt = (f"Vízszintes sávdiagram: a(z) '{q}' téma átlagos tálalás-szentimentje "
                   f"médiarégiónként ({len(labels)} régió; legpozitívabb: {labels[0]}, "
                   f"legnegatívabb: {labels[-1]}). Adatforrás: Echolot régiós keretezés-elemzés, 7 nap.")
        png = render_diverging_hbar(title, labels, values, source=source)
        return png, (alt if png else "")

    art_rows = [(l, a) for l, _, a in rows if a is not None and a > 0]
    if len(art_rows) >= 2:
        art_rows.sort(key=lambda r: r[1], reverse=True)
        art_rows = art_rows[:12]
        labels = [r[0] for r in art_rows]
        values = [float(r[1]) for r in art_rows]
        if lang == "en":
            title = f"Coverage volume by region: {q}"[:110]
            source = "Data: Echolot regional framing analysis, last 7 days"
            alt = (f"Bar chart of article counts by media region for '{q}' "
                   f"({len(labels)} regions). Data: Echolot regional framing analysis, 7 days.")
        else:
            title = f"Lefedettség régiónként: {q}"[:110]
            source = "Adat: Echolot régiós keretezés-elemzés, elmúlt 7 nap"
            alt = (f"Sávdiagram: a(z) '{q}' téma cikkszáma médiarégiónként "
                   f"({len(labels)} régió). Adatforrás: Echolot régiós keretezés-elemzés, 7 nap.")
        # magnitúdó → egy hue-s vízszintes bar (diverging renderer 1 színnel visszaélne)
        png = render_bar_chart(title, labels, values, source=source)
        return png, (alt if png else "")
    return None, ""
