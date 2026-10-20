"""Picture layout for reports: the web app's light theme (same color tokens), drawn with matplotlib.
Pictures are 1080 px wide (Telegram shows them sharp on phones)."""

from __future__ import annotations

import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

BG, SURFACE, BORDER = "#f6f6f4", "#fcfcfb", "#e6e5e0"
TEXT, TEXT2, TEXT3 = "#0b0b0b", "#52514e", "#7a7974"
GOOD, CRITICAL, WARNING = "#0ca30c", "#d03b3b", "#fab219"
ACCENT, ACCENT2, MUTED = "#2a6fdb", "#9aa7b8", "#c9c8c2"
DPI = 150
WIDTH_IN = 1080 / DPI

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": BORDER, "axes.labelcolor": TEXT2,
    "xtick.color": TEXT3, "ytick.color": TEXT3, "axes.facecolor": SURFACE, "figure.facecolor": BG,
    "axes.grid": True, "grid.color": BORDER, "grid.linewidth": 0.6, "axes.spines.top": False,
    "axes.spines.right": False, "axes.titlesize": 10, "axes.titleweight": "bold", "axes.titlecolor": TEXT,
    "axes.titlelocation": "left", "axes.titlepad": 8,
})


def figure(height_px: int):
    return plt.figure(figsize=(WIDTH_IN, height_px / DPI), dpi=DPI)


def change_color(x, flat: float = 0.0005) -> str:
    """Green up, red down, grey when (almost) unchanged or unknown."""
    return TEXT3 if x is None or x != x or abs(x) < flat else GOOD if x > 0 else CRITICAL


def pct(x, digits: int = 1, sign: bool = True) -> str:
    if x is None or x != x:
        return "n/a"
    return f"{x * 100:+.{digits}f}%" if sign else f"{x * 100:.{digits}f}%"


def money(x) -> str:
    if x is None or x != x:
        return "n/a"
    a = abs(x)
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if a >= div:
            return f"${x / div:,.2f}{unit}"
    return f"${x:,.2f}"


def card(fig, rect, title: str | None = None):
    """A rounded 'card' background on the figure; returns an axes for its content (no frame)."""
    x, y, w, h = rect
    fig.patches.append(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.012",
                                      transform=fig.transFigure, facecolor=SURFACE, edgecolor=BORDER, linewidth=0.8,
                                      zorder=-10))     # behind every axes, so the card's content shows
    ax = fig.add_axes((x + 0.02, y + 0.012, w - 0.04, h - 0.024))
    ax.set_zorder(5)
    ax.set_axis_off()
    if title:
        ax.text(0, 1, title, transform=ax.transAxes, fontsize=10.5, fontweight="bold", color=TEXT, va="top")
    return ax


def wrap(text: str, width: int) -> str:
    return "\n".join(textwrap.fill(p, width) for p in str(text).split("\n"))


def header(fig, title: str, subtitle: str, right_big: str | None = None, right_small: str | None = None,
           right_color: str = TEXT, top: float = 0.985, height: float = 0.07):
    ax = fig.add_axes((0.04, top - height, 0.92, height))
    ax.set_axis_off()
    ax.text(0, 0.95, title, fontsize=17, fontweight="bold", color=TEXT, va="top")
    ax.text(0, 0.18, subtitle, fontsize=9.5, color=TEXT2, va="bottom")
    if right_big:
        ax.text(1, 0.95, right_big, fontsize=17, fontweight="bold", color=TEXT, va="top", ha="right")
    if right_small:
        ax.text(1, 0.18, right_small, fontsize=10, color=right_color, va="bottom", ha="right", fontweight="bold")
    return ax


def footer(fig, text: str):
    fig.text(0.04, 0.008, text, fontsize=7, color=TEXT3, va="bottom")


def tile(ax, x, y, w, h, label: str, value: str, sub: str, color: str):
    """A small tile: label on top, the big (colored) number, a grey detail line at the bottom."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.03", transform=ax.transAxes,
                                facecolor="white", edgecolor=BORDER, linewidth=0.8))
    pad = 0.08 * w
    ax.text(x + pad, y + h * 0.80, label, transform=ax.transAxes, fontsize=8, color=TEXT2, va="center")
    ax.text(x + pad, y + h * 0.48, value, transform=ax.transAxes, fontsize=14, fontweight="bold", color=color, va="center")
    ax.text(x + pad, y + h * 0.17, sub, transform=ax.transAxes, fontsize=7.8, color=TEXT3, va="center")


def save(fig, path) -> str:
    fig.savefig(path, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return str(path)
