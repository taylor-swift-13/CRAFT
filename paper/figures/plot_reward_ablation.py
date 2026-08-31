#!/usr/bin/env python3
"""Generate the reward-function ablation figure (RQ5).

Budget grid: k in {1, 4, 8, 16, 32}, matching the cluster ablation runs
(Zero-initialized Qwen3-8B on the curated pool); the untrained model is
the matched base-probe curve, drawn dotted.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


OUT = Path(__file__).resolve().parent

K = [1, 4, 8, 16, 32]

VARIANTS = {
    "Binary": {
        "pass": [3.78, 6.21, 7.72, 9.40, 11.26],
        "combine": [7.21, 12.26, 16.71, 20.31, 23.32],
        "color": "#9D6652",   # muted terracotta
        "marker": "o",
        "style": "-",
    },
    "Whole-rollout": {
        "pass": [16.67, 19.17, 20.03, 20.90, 21.92],
        "combine": [18.87, 19.95, 20.31, 21.27, 22.72],
        "color": "#A17B45",   # muted ochre
        "marker": "s",
        "style": "-",
    },
    "Clause-decomposed": {
        "pass": [4.10, 9.35, 12.27, 14.88, 17.14],
        "combine": [48.08, 57.33, 60.22, 61.54, 61.90],
        "color": "#708C7C",   # desaturated sage
        "marker": "^",
        "style": "-",
    },
    "Full (ours)": {
        "pass": [6.80, 13.41, 16.80, 20.17, 23.44],
        "combine": [57.93, 66.95, 70.43, 72.60, 73.20],
        "color": "#2D7053",   # paper deep green
        "marker": "D",
        "style": "-",
    },
}

UNTRAINED = {
    "pass": [6.43, 13.24, 17.33, 21.62, 25.96],
    "combine": [37.86, 50.60, 54.21, 56.37, 57.81],
}


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
    ax.set_ylabel("Verification rate (%)")
    ax.grid(True, which="major", alpha=0.72)
    ax.tick_params(color="#92A39A", width=0.6)
    ax.spines[["top", "right"]].set_visible(False)


def plot_panel(ax: plt.Axes, metric: str) -> None:
    ax.plot(
        K,
        UNTRAINED[metric],
        label="Zero (untrained)",
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
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.45))
    plot_panel(axes[0], "combine")
    plot_panel(axes[1], "pass")
    style_axis(axes[0])
    style_axis(axes[1])
    axes[0].set_title("(a) compose@$k$: compositional coverage", pad=8)
    axes[1].set_title("(b) pass@$k$: response-level coverage", pad=8)
    axes[0].set_ylim(0, 80)
    axes[1].set_ylim(0, 36)
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
