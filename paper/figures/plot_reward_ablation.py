#!/usr/bin/env python3
"""Generate the reward-function ablation figure (RQ5).

Budget grid: k in {1, 4, 8, 16, 32}.  Only the k=1 entries below are
measured; every other entry is a placeholder (None) until the new-grid
recomputation finishes.  The guard below refuses to render a figure from
placeholder data.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


OUT = Path(__file__).resolve().parent

K = [1, 4, 8, 16, 32]

# k=1 values are measured; None marks the k in {4, 8, 16, 32} slots that
# still await the new-grid recomputation.
VARIANTS = {
    "Binary": {
        "pass": [8.65, None, None, None, None],
        "combine": [1.80, None, None, None, None],
        "color": "#9D6652",   # muted terracotta
        "marker": "o",
        "style": "-",
    },
    "Whole-rollout": {
        "pass": [10.80, None, None, None, None],
        "combine": [22.24, None, None, None, None],
        "color": "#A17B45",   # muted ochre
        "marker": "s",
        "style": "-",
    },
    "Clause-decomp.": {
        "pass": [2.66, None, None, None, None],
        "combine": [29.57, None, None, None, None],
        "color": "#708C7C",   # desaturated sage
        "marker": "^",
        "style": "-",
    },
    "+Shapley": {
        "pass": [2.31, None, None, None, None],
        "combine": [30.77, None, None, None, None],
        "color": "#2D7053",   # paper deep green
        "marker": "D",
        "style": "-",
    },
}

UNTRAINED = {
    "pass": [3.55, None, None, None, None],
    "combine": [26.32, None, None, None, None],
}


def require_complete() -> None:
    """SystemExit on placeholder data so stale plots are never rendered."""
    series = dict(VARIANTS)
    missing = [
        f"{label}.{metric}"
        for label, values in series.items()
        for metric in ("pass", "combine")
        if values[metric] is None or any(v is None for v in values[metric])
    ]
    missing += [
        f"Untrained.{metric}"
        for metric in ("pass", "combine")
        if UNTRAINED[metric] is None or any(v is None for v in UNTRAINED[metric])
    ]
    if missing:
        raise SystemExit(
            f"plot_reward_ablation: placeholder data on the k={K} grid; "
            "fill from the new-grid recomputation first: " + ", ".join(missing)
        )


def configure() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10.7,
            "axes.titlesize": 11.8,
            "axes.labelsize": 10.8,
            "legend.fontsize": 9.3,
            "xtick.labelsize": 9.8,
            "ytick.labelsize": 9.8,
            "axes.edgecolor": "#5c6e65",
            "axes.linewidth": 0.7,
            "axes.facecolor": "#F7FAF8",
            "figure.facecolor": "white",
            "grid.color": "#D5E0D9",
            "grid.linewidth": 0.55,
            "pdf.fonttype": 42,
        }
    )


def style_axis(ax: plt.Axes) -> None:
    ax.set_xscale("log")
    ax.set_xticks(K)
    ax.set_xticklabels([str(k) for k in K])
    ax.set_xlabel("Number of responses, $k$")
    ax.set_ylabel("Verification rate (\%)")
    ax.grid(True, which="major", alpha=0.72)
    ax.tick_params(color="#92A39A", width=0.6)
    ax.spines[["top", "right"]].set_visible(False)


def plot_panel(ax: plt.Axes, metric: str) -> None:
    ax.plot(
        K,
        UNTRAINED[metric],
        label="Untrained 8B",
        color="#737B77",
        marker="x",
        linestyle=":",
        linewidth=1.7,
        markersize=5.2,
        markeredgewidth=1.2,
        alpha=0.9,
    )
    for label, values in VARIANTS.items():
        ax.plot(
            K,
            values[metric],
            label=label,
            color=values["color"],
            marker=values["marker"],
            linestyle=values["style"],
            linewidth=2.0,
            markersize=5.4,
            markeredgewidth=0.7,
            markeredgecolor="white",
        )


def reward_ablation() -> None:
    require_complete()
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.45))
    plot_panel(axes[0], "combine")
    plot_panel(axes[1], "pass")
    style_axis(axes[0])
    style_axis(axes[1])
    axes[0].set_title("(a) compose@$k$: compositional coverage", pad=8)
    axes[1].set_title("(b) pass@$k$: response-level coverage", pad=8)
    axes[0].set_ylim(0, 44)
    axes[1].set_ylim(0, 17)
    for ax in axes:
        ax.set_xlim(0.85, 38)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=5, frameon=False,
               handlelength=2.4, columnspacing=1.35, bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout(rect=(0, 0, 1, 0.88), w_pad=2.4)
    fig.savefig(OUT / "reward_ablation.pdf", bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    configure()
    reward_ablation()
