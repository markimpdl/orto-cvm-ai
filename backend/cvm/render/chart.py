"""Chart scenes drawn from the script's structured data (`visual.chart`), as a still for the composer.

kinds: "bar" (label + value, e.g. 0–96 km/h times), "timeline" (label + text, in order), "stat" (one big number).
The picture leaves the top (title overlay) and the bottom (captions) clear.
"""

from pathlib import Path

from cvm.render.captions import SOURCE_LABEL

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

BG, INK, MUTED, GO, GO_SOFT = "#F5F5F2", "#111214", "#5B6068", "#00843D", "#B9DFC8"
FONT = "Arial"


def _fmt(v: float, unit: str) -> str:
    s = f"{v:,.2f}".rstrip("0").rstrip(".").replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} {unit}".strip()


def render_chart(chart: dict, W: int, H: int, margin_bottom: int, out: Path, language: str = "pt-BR") -> Path | None:
    """Draw the chart into a W×H PNG; None when the data is unusable (the composer falls back to a text card)."""
    kind, items = chart.get("kind", "bar"), [i for i in chart.get("items") or [] if i.get("label")]
    if kind == "bar":
        items = [i for i in items if isinstance(i.get("value"), (int, float))]
    if not items or (kind == "stat" and not (items[0].get("value") is not None or items[0].get("text"))):
        return None
    if FONT not in {f.name for f in font_manager.fontManager.ttflist}:
        plt.rcParams["font.family"] = "sans-serif"
    else:
        plt.rcParams["font.family"] = FONT

    dpi = 100
    fig = plt.figure(figsize=(W / dpi, H / dpi), dpi=dpi, facecolor=BG)
    portrait = H > W
    top = 0.80 if portrait else 0.78  # title overlay + chart title above
    bottom = (margin_bottom + H * (0.10 if portrait else 0.12)) / H  # captions below
    left, right = (0.08, 0.92) if portrait else (0.12, 0.88)
    base = H * (0.022 if portrait else 0.03)  # font size in px
    pt = lambda k: base * k * 72 / dpi  # noqa: E731
    unit = chart.get("unit", "")

    if chart.get("title"):
        fig.text(0.5, top + 0.035, chart["title"], ha="center", va="bottom", fontsize=pt(1.25), weight="bold", color=INK, wrap=True)
    if chart.get("source"):
        fig.text(0.5, bottom - 0.02, f"{SOURCE_LABEL.get(language, 'Fonte')}: {chart['source']}", ha="center", va="top", fontsize=pt(0.6), color=MUTED)

    ax = fig.add_axes((left, bottom, right - left, top - bottom))
    ax.set_facecolor(BG)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xticks([])
    ax.set_yticks([])

    if kind == "bar":
        vals = [float(i["value"]) for i in items]
        hi = max(vals) or 1
        n = len(items)
        ys = list(range(n))[::-1]
        colors = [GO if i.get("highlight") or (not any(x.get("highlight") for x in items) and v == max(vals)) else GO_SOFT for i, v in zip(items, vals)]
        ax.barh(ys, vals, height=0.55, color=colors)
        for y, i, v in zip(ys, items, vals):
            ax.text(0, y + 0.42, i["label"], va="bottom", ha="left", fontsize=pt(0.85), color=INK)
            ax.text(v + hi * 0.02, y, _fmt(v, unit), va="center", ha="left", fontsize=pt(1.05), weight="bold", color=INK)
        ax.set_xlim(0, hi * 1.35)
        ax.set_ylim(-0.6, n - 0.2)
    elif kind == "timeline":
        n = len(items)
        ax.set_xlim(0, 1)
        ax.set_ylim(n - 0.5, -0.5)
        ax.plot([0.06, 0.06], [0, n - 1], color=GO_SOFT, lw=base * 0.25, zorder=1)
        for y, i in enumerate(items):
            last = y == n - 1
            ax.scatter([0.06], [y], s=(base * (1.3 if last else 0.9)) ** 2, color=GO if last else "white", edgecolors=GO, linewidths=base * 0.15, zorder=2)
            ax.text(0.13, y, i["label"], va="center", ha="left", fontsize=pt(0.95), weight="bold", color=INK)
            ax.text(0.13 + (0.30 if portrait else 0.22), y, i.get("text") or _fmt(i.get("value", 0), unit), va="center", ha="left",
                    fontsize=pt(0.95), weight="bold" if last else "normal", color=GO if last else INK)
    else:  # stat
        i = items[0]
        big = i.get("text") or _fmt(float(i["value"]), unit)
        ax.text(0.5, 0.58, big, ha="center", va="center", fontsize=pt(3.4), weight="bold", color=GO)
        ax.text(0.5, 0.30, i["label"], ha="center", va="center", fontsize=pt(1.05), color=INK, wrap=True)

    fig.savefig(out, dpi=dpi, facecolor=BG)
    plt.close(fig)
    return out
