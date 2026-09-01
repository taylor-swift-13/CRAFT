#!/usr/bin/env python3
"""Shared visual style for every CRAFT paper figure.

One palette for all matplotlib PDFs and the overview SVG, so the paper reads
as a single visual system.  Keep in sync with:
  - the \\definecolor block in paper/main.tex (green/teal/olive/rust family)
  - the <style> tokens in paper/figures/overview.html

Usage in a plot script:
    from paper_style import GREEN, RUST, ..., use_paper_style
    use_paper_style()
"""

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


def use_paper_style(base_size: float = 8.2) -> None:
    """Matplotlib rcParams shared by all CRAFT data figures."""
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": base_size,
            "axes.titlesize": base_size + 1.0,
            "axes.labelsize": base_size + 0.3,
            "legend.fontsize": base_size - 0.6,
            "xtick.labelsize": base_size - 0.5,
            "ytick.labelsize": base_size - 0.5,
            "axes.edgecolor": MUTED,
            "axes.linewidth": 0.6,
            "axes.facecolor": "white",
            "figure.facecolor": "white",
            "grid.color": FAINT,
            "grid.linewidth": 0.55,
            "pdf.fonttype": 42,
        }
    )
