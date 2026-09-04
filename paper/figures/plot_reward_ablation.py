#!/usr/bin/env python3
"""Generate the reward-function ablation figure (RQ5).

Budget grid: k in {1, 4, 8, 16, 32}.  The top row reports Qwen3-8B reward
ablations initialized from Bare; the bottom row reports their matched
SFT-initialized counterparts.  Reference checkpoints before RL are dotted.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from paper_style import GREEN, MUTED, OCHRE, RUST, TEAL, use_paper_style


OUT = Path(__file__).resolve().parent

K = [1, 4, 8, 16, 32]

VARIANTS = {
    "Binary": {
        "pass": [3.78, 6.21, 7.72, 9.40, 11.26],
        "combine": [7.21, 12.26, 16.71, 20.31, 23.32],
        "color": RUST,   # baseline family
        "marker": "o",
        "style": "-",
    },
    "Whole-rollout": {
        "pass": [16.67, 19.17, 20.03, 20.90, 21.92],
        "combine": [18.87, 19.95, 20.31, 21.27, 22.72],
        "color": OCHRE,  # weak ablation
        "marker": "s",
        "style": "-",
    },
    "Clause-decomposed": {
        "pass": [4.10, 9.35, 12.27, 14.88, 17.14],
        "combine": [48.08, 57.33, 60.22, 61.54, 61.90],
        "color": TEAL,   # decomposed ablation
        "marker": "^",
        "style": "-",
    },
    "Full (ours)": {
        "pass": [6.80, 13.41, 16.80, 20.17, 23.44],
        "combine": [57.93, 66.95, 70.43, 72.60, 73.20],
        "color": GREEN,  # ours
        "marker": "D",
        "style": "-",
    },
}

UNTRAINED = {
    "pass": [6.43, 13.24, 17.33, 21.62, 25.96],
    "combine": [37.86, 50.60, 54.21, 56.37, 57.81],
}

SFT_REFERENCE = {
    "pass": [41.93, 56.82, 61.11, 64.68, 67.98],
    "combine": [63.90, 73.80, 76.20, 77.30, 77.90],
}

SFT_VARIANTS = {
    "Binary": {
        "pass": [13.90, 16.40, 17.60, 18.60, 19.50],
        "combine": [13.70, 16.10, 17.90, 18.80, 19.70],
        "color": RUST,
        "marker": "o",
        "style": "-",
    },
    "Clause-decomposed": {
        "pass": [31.93, 44.91, 49.57, 53.18, 56.04],
        "combine": [67.43, 75.12, 77.64, 78.61, 78.73],
        "color": TEAL,
        "marker": "^",
        "style": "-",
    },
    "Full (ours)": {
        "pass": [30.78, 43.44, 47.47, 50.63, 53.47],
        "combine": [69.23, 75.24, 76.80, 77.64, 78.37],
        "color": GREEN,
        "marker": "D",
        "style": "-",
    },
}


def configure() -> None:
    use_paper_style()


def style_axis(ax: plt.Axes) -> None:
    ax.set_xscale("log")
    ax.set_xticks(K)
    ax.set_xticklabels([str(k) for k in K])
    ax.set_xlabel("Number of responses, $k$")
    ax.set_ylabel("Verification rate (%)")
    ax.grid(True, which="major", alpha=0.72)
    ax.tick_params(color="#92A39A", width=0.6)
    ax.spines[["top", "right"]].set_visible(False)


def plot_panel(
    ax: plt.Axes, metric: str, reference: dict, variants: dict
) -> None:
    ax.plot(
        K,
        reference[metric],
        label="Before RL",
        color=MUTED,
        marker="x",
        linestyle=":",
        linewidth=1.7,
        markersize=5.2,
        markeredgewidth=1.2,
        alpha=0.9,
    )
    for label, values in variants.items():
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
    fig, axes = plt.subplots(1, 2, figsize=(7.15, 2.55))
    panels = [
        (axes[0], "combine", UNTRAINED, VARIANTS,
         "(a) Bare: compose@$k$", (0, 80)),
        (axes[1], "pass", UNTRAINED, VARIANTS,
         "(b) Bare: pass@$k$", (0, 36)),
    ]
    for panel_idx, (ax, metric, reference, variants, title, ylim) in enumerate(panels):
        plot_panel(ax, metric, reference, variants)
        style_axis(ax)
        ax.set_xlabel("Responses, $k$")
        if panel_idx:
            ax.set_ylabel("")
        ax.set_title(title, pad=7)
        ax.set_ylim(*ylim)
        ax.set_xlim(0.85, 38)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=5, frameon=False,
               handlelength=2.4, columnspacing=1.35, bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout(rect=(0, 0, 1, 0.84), w_pad=1.2)
    fig.savefig(OUT / "reward_ablation.pdf", bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    configure()
    reward_ablation()
