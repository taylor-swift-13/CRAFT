#!/usr/bin/env python3
"""Plot the model-matched verification--token Pareto comparison for RQ3."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from paper_style import FAINT, GREEN, INK, MUTED, OCHRE, use_paper_style


OUT = Path(__file__).resolve().parent

# All points use GPT-5-nano with reasoning disabled.  Token counts are the
# archived mean total-token counts per task with at least one model call.
BASELINES = {
    "AutoSpec": (2181.72, 42.19),
    "SESpec": (22081.61, 29.69),
    "Clause2Inv": (1056.25, 7.93),
    "Loopy": (14513.37, 18.75),
}
NAIVE = (225.95, 16.47)
OURS = {
    "@1": (1136.82, 33.89),
    "@4": (1905.10, 49.04),
    "@8": (2929.47, 55.53),
}


def main() -> None:
    use_paper_style(base_size=8.6)
    plt.rcParams.update(
        {
            "axes.labelsize": 8.8,
            "xtick.labelsize": 7.8,
            "ytick.labelsize": 7.8,
        }
    )

    fig, ax = plt.subplots(figsize=(4.0, 2.3))

    bx = [value[0] for value in BASELINES.values()]
    by = [value[1] for value in BASELINES.values()]
    ax.scatter(
        bx,
        by,
        s=42,
        marker="o",
        color=OCHRE,
        edgecolor="white",
        linewidth=0.7,
        zorder=3,
    )
    ax.scatter(
        [NAIVE[0]],
        [NAIVE[1]],
        s=39,
        marker="o",
        color=MUTED,
        edgecolor="white",
        linewidth=0.7,
        zorder=3,
    )

    ox = [value[0] for value in OURS.values()]
    oy = [value[1] for value in OURS.values()]
    ax.plot(ox, oy, color=GREEN, linewidth=2.0, zorder=3)
    ax.scatter(
        ox,
        oy,
        s=58,
        marker="D",
        color=GREEN,
        edgecolor="white",
        linewidth=0.8,
        zorder=4,
    )

    # The discrete upper-left frontier consists of direct prompting followed
    # by the three CRAFT budgets; the dotted segment is only a visual guide.
    ax.plot(
        [NAIVE[0], *ox],
        [NAIVE[1], *oy],
        color=GREEN,
        linewidth=1.0,
        linestyle=(0, (2, 2)),
        alpha=0.58,
        zorder=2,
    )

    baseline_offsets = {
        "AutoSpec": (6, -2),
        "SESpec": (-43, 6),
        "Clause2Inv": (-17, 7),
        "Loopy": (-34, 6),
    }
    for name, (x, y) in BASELINES.items():
        ax.annotate(
            name,
            (x, y),
            xytext=baseline_offsets[name],
            textcoords="offset points",
            color=INK,
            fontsize=7.4,
        )
    ax.annotate(
        "Naive",
        NAIVE,
        xytext=(6, 5),
        textcoords="offset points",
        color=INK,
        fontsize=7.4,
    )
    for index, (budget, (x, y)) in enumerate(OURS.items()):
        ax.annotate(
            ("CRAFT " if index == 0 else "") + budget,
            (x, y),
            xytext=(7, -1),
            textcoords="offset points",
            va="center",
            color=GREEN,
            fontsize=7.7,
            fontweight="bold",
        )

    ax.set_xscale("log")
    ax.set_xlim(170, 30000)
    ax.set_ylim(3, 60)
    ax.set_xticks([200, 1000, 2000, 10000, 20000])
    ax.set_xticklabels(["0.2k", "1k", "2k", "10k", "20k"])
    ax.set_yticks([10, 20, 30, 40, 50, 60])
    ax.set_xlabel(r"Mean total tokens / called task  $\downarrow$")
    ax.set_ylabel(r"Programs verified (%)  $\uparrow$")
    ax.grid(axis="y", color=FAINT, linewidth=0.55, alpha=0.9)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(color="#92A39A", width=0.6)

    fig.tight_layout(pad=0.35)
    fig.savefig(OUT / "tool_pareto.pdf", bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main()
