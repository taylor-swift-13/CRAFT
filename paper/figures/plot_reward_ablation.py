#!/usr/bin/env python3
"""Generate the reward-function ablation figure (RQ5)."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


OUT = Path(__file__).resolve().parent

K = [1, 10, 30, 50, 100]

VARIANTS = {
    "Binary": {
        "pass": [8.65, 10.84, 12.09, 12.66, 13.46],
        "combine": [1.80, 5.89, 8.53, 9.98, 12.02],
        "color": "#9d6652",   # codered (paper palette)
        "marker": "P",
        "style": "--",
    },
    "Whole-rollout": {
        "pass": [10.80, 12.25, 12.81, 13.10, 13.46],
        "combine": [22.24, 24.88, 25.72, 25.96, 26.56],
        "color": "#b87a2a",   # codeolive (paper palette)
        "marker": "o",
        "style": "-.",
    },
    "Clause-decomp.": {
        "pass": [2.66, 6.32, 7.65, 8.10, 8.65],
        "combine": [29.57, 34.74, 37.14, 38.22, 39.06],
        "color": "#5b7185",   # slate blue (paper palette)
        "marker": "D",
        "style": (0, (5, 1)),
    },
    "+Shapley": {
        "pass": [2.31, 5.35, 6.59, 6.97, 7.33],
        "combine": [30.77, 36.06, 38.46, 39.30, 41.11],
        "color": "#2d7053",   # codeblue/deep green (paper palette)
        "marker": "^",
        "style": "-",
    },
}

UNTRAINED = {
    "pass": [3.55, 9.86, 12.78, 13.83, 15.02],
    "combine": [26.32, 36.78, 38.22, 39.06, 39.78],
}

# vertical nudges (points) for end-of-line labels per panel
LABEL_OFFSETS = {
    "pass": {"Binary": 3.8, "Whole-rollout": -3.8},
    "combine": {"+Shapley": 3.0, "Clause-decomp.": -3.0},
}


def configure() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11.2,
            "axes.titlesize": 12.6,
            "axes.labelsize": 11.6,
            "legend.fontsize": 10.2,
            "xtick.labelsize": 10.6,
            "ytick.labelsize": 10.6,
            "axes.edgecolor": "#5c6e65",
            "axes.linewidth": 0.7,
            "grid.color": "#dce5df",
            "grid.linewidth": 0.6,
            "pdf.fonttype": 42,
        }
    )


def style_axis(ax: plt.Axes) -> None:
    ax.set_xscale("log")
    ax.set_xticks(K)
    ax.set_xticklabels([str(k) for k in K])
    ax.set_xlabel("Number of responses, $k$")
    ax.set_ylabel("Verification rate (\%)")
    ax.grid(True, which="major", alpha=0.8)
    ax.spines[["top", "right"]].set_visible(False)


def plot_panel(ax: plt.Axes, metric: str) -> None:
    ax.plot(
        K,
        UNTRAINED[metric],
        label="Untrained 8B",
        color="#6f6f6f",
        marker="x",
        linestyle=":",
        linewidth=2.0,
        markersize=6.0,
        markeredgewidth=1.5,
    )
    for label, values in VARIANTS.items():
        ax.plot(
            K,
            values[metric],
            label=label,
            color=values["color"],
            marker=values["marker"],
            linestyle=values["style"],
            linewidth=2.3,
            markersize=6.0,
            markeredgewidth=0.8,
            markeredgecolor="white",
        )
        # nudge end labels apart when terminal values nearly coincide
        dy = LABEL_OFFSETS.get(metric, {}).get(label, 0.0)
        ax.annotate(
            label,
            xy=(K[-1], values[metric][-1]),
            xytext=(4, dy),
            textcoords="offset points",
            fontsize=9.4,
            color=values["color"],
            va="center",
        )


def reward_ablation() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.5))
    plot_panel(axes[0], "pass")
    plot_panel(axes[1], "combine")
    style_axis(axes[0])
    style_axis(axes[1])
    axes[0].set_title("(a) pass@$k$ (single response)")
    axes[1].set_title("(b) compose@$k$ (union + Houdini)")
    axes[0].set_ylim(0, 20)
    axes[1].set_ylim(0, 48)
    for ax in axes:
        ax.set_xlim(K[0], K[-1] * 2.9)
    handles, labels = axes[1].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=5, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 0.87), w_pad=2.8)
    fig.savefig(OUT / "reward_ablation.pdf", bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    configure()
    reward_ablation()
