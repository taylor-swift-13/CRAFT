#!/usr/bin/env python3
"""Shared visual style for every CRAFT paper figure.

One palette for all matplotlib PDFs and the overview SVG, so the paper reads
as a single visual system.  Keep in sync with:
  - the \\definecolor block in paper/main.tex (green/teal/olive/rust family)
  - the <style> tokens in paper/sections/fig_overview.tex

Usage in a plot script:
    from paper_style import GREEN, RUST, ..., use_paper_style
    use_paper_style()
"""

# Shared native sizes.  Fixed panel canvases preserve the printed scale.
FONT_SIZE = 9.0
LINE_WIDTH = 1.4
MARKER_SIZE = 3.8
MARKER_EDGE_WIDTH = 0.5
AXIS_WIDTH = 0.6
GRID_WIDTH = 0.55

# ---------------------------------------------------------------- core hues
GREEN = "#2E7D5B"   # verified / ours / primary method
TEAL = "#347F78"    # SFT stage, secondary series
OCHRE = "#B87A2C"   # scorer / weak-baseline series
SLATE = "#5B7185"   # RL stage, "before" series
RUST = "#B85C47"    # rejected / baseline series
INK = "#1F3128"     # text
MUTED = "#66786F"   # secondary text, axes, untrained reference
FAINT = "#D8E2DC"   # hairlines, grids

# ------------------------------------------------- tints (fills, grid cells)
GREEN_TINT = "#E8F3EC"
TEAL_TINT = "#E6F2F0"
OCHRE_TINT = "#F8F0E2"
SLATE_TINT = "#E9EEF3"
RUST_TINT = "#F6E3DF"
PANEL_BG = "#FBFCFB"

# --------------------------- clause identity shades (overview figure only)
# Six distinguishable greens; a clause keeps its color across
# rollout -> decompose -> pool so provenance is visually traceable.
CLAUSE = ["#2E7D5B", "#4C9A74", "#7FB99C", "#A9D0BD", "#3A8F83", "#62A98A"]


def use_paper_style(base_size: float = 10.0) -> None:
    """Matplotlib rcParams shared by all CRAFT data figures."""
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": "Nimbus Sans",
            "font.size": base_size,
            "axes.titlesize": base_size + 0.5,
            "axes.labelsize": base_size,
            "legend.fontsize": base_size,
            "xtick.labelsize": base_size - 0.8,
            "ytick.labelsize": base_size - 0.8,
            "axes.edgecolor": MUTED,
            "text.color": INK,
            "axes.labelcolor": INK,
            "axes.titlecolor": INK,
            "axes.titlelocation": "left",
            "axes.titlepad": 10,
            "axes.labelpad": 7,
            "axes.axisbelow": True,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "legend.frameon": False,
            "legend.handlelength": 2.5,
            "lines.linewidth": LINE_WIDTH,
            "lines.markersize": MARKER_SIZE,
            "axes.linewidth": AXIS_WIDTH,
            "axes.facecolor": "white",
            "figure.facecolor": "white",
            "grid.color": FAINT,
            "grid.linewidth": GRID_WIDTH,
            "pdf.fonttype": 42,
        }
    )


# Physical reference: the Bare probe (Figure 6).  One cell is now printed
# at one third of textwidth; two at two thirds, three at full textwidth.
# Scale text by 3/4 to retain the original Figure 6 printed type size.
# Fixed canvases, rather than tight cropping, preserve the same PDF scale.
PANEL_CELL_WIDTH = 520.4868774414062 / 72 / 4
PANEL_HEIGHT = 175.71600341796875 / 72
AXES_WIDTH = 7.2 * (0.985 - 0.07) / (4 + 3 * 0.22)
AXES_HEIGHT = 2.35 * (0.73 - 0.24)


def panel_figure(count):
    """Return equally sized plotting areas at the Figure 6 type scale."""
    import matplotlib.pyplot as plt
    use_paper_style(FONT_SIZE)
    plt.rcParams.update({
        "axes.titlesize": FONT_SIZE, "axes.titlepad": 5,
        "axes.labelpad": 1, "legend.fontsize": FONT_SIZE,
        "xtick.major.size": 2.5, "ytick.major.size": 2.5,
        "xtick.major.pad": 1.5, "ytick.major.pad": 1.5,
        "legend.handlelength": 1.7, "lines.markersize": MARKER_SIZE,
        "xtick.labelsize": FONT_SIZE, "ytick.labelsize": FONT_SIZE,
    })
    width = count * PANEL_CELL_WIDTH
    fig = plt.figure(figsize=(width, PANEL_HEIGHT))
    axes = [fig.add_axes([(i * PANEL_CELL_WIDTH + 0.355) / width,
                         0.58 / PANEL_HEIGHT, AXES_WIDTH / width,
                         AXES_HEIGHT / PANEL_HEIGHT]) for i in range(count)]
    return fig, axes


def panel_legend(fig, ax, columns=2):
    handles, labels = ax.get_legend_handles_labels()
    return fig.legend(handles, labels, loc="upper center", ncol=columns,
                      bbox_to_anchor=(0.5, 1.0), borderaxespad=0,
                      columnspacing=0.9, handletextpad=0.5, labelspacing=0.3)


def save_panel_figure(fig, output, stem, *, normalize_linewidth=True):
    """Save without changing canvas width; record geometry for PDF checks."""
    import json
    from matplotlib.text import Text
    from matplotlib.lines import Line2D
    from matplotlib.collections import PathCollection
    # Normalize data symbols and series while preserving axes/ticks/error bars.
    series = [line for ax in fig.axes for line in ax.lines]
    series += [item for item in fig.artists if isinstance(item, Line2D)]
    for legend in fig.legends:
        series += [h for h in legend.legend_handles if isinstance(h, Line2D)]
    for line in series:
        if normalize_linewidth and line.get_marker() not in ('_', '|'):
            line.set_linewidth(LINE_WIDTH)
        if line.get_marker() in ('o', 's', 'D', 'd', '^', 'v', '<', '>', 'x', '+'):
            line.set_markersize(MARKER_SIZE)
            line.set_markeredgewidth(MARKER_EDGE_WIDTH)
            line.set_markeredgecolor(line.get_color() if line.get_marker() in ('x', '+') else 'white')
    for ax in fig.axes:
        for collection in ax.collections:
            if isinstance(collection, PathCollection):
                collection.set_sizes([MARKER_SIZE**2])
                collection.set_linewidths([MARKER_EDGE_WIDTH])
    for text in fig.findobj(match=Text):
        text.set_fontsize(FONT_SIZE * 0.75)
    for suffix in ("pdf", "png"):
        fig.savefig(output / f"{stem}.{suffix}", dpi=240)
    size = fig.get_size_inches()
    geometry = {"canvas_inches": size.tolist(), "panels": [
        {"width_inches": ax.get_position().width * size[0],
         "height_inches": ax.get_position().height * size[1],
         "label_font_points": ax.xaxis.label.get_fontsize(),
         "tick_font_points": ax.xaxis.get_ticklabels()[0].get_fontsize()}
        for ax in fig.axes]}
    (output / f"{stem}.geometry.json").write_text(json.dumps(geometry, indent=2)+"\n")
