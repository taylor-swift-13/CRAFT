#!/usr/bin/env python3
"""Generate the Qwen exploration and RL-Zero figures used by the paper."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


OUT = Path(__file__).resolve().parent

ROLLOUT_K = [1, 10, 30, 50, 100]

OFFICIAL = {
    "Qwen3-4B": {
        "direct": [2.53, 7.56, 11.21, 13.10, 15.62],
        "combine": [33.89, 45.31, 47.24, 47.60, 47.84],
        "color": "#9d6652",
        "marker": "o",
        "style": "-",
    },
    "Qwen3-8B": {
        "direct": [9.30, 22.87, 28.30, 30.47, 33.29],
        "combine": [40.26, 52.40, 55.05, 55.17, 55.41],
        "color": "#4f8a6b",
        "marker": "s",
        "style": "--",
    },
    "Qwen3-14B": {
        "direct": [10.29, 25.45, 33.55, 36.78, 39.90],
        "combine": [49.52, 57.33, 57.81, 57.81, 58.05],
        "color": "#2d7053",
        "marker": "^",
        "style": "-.",
    },
    "Qwen3-30B-A3B": {
        "direct": [7.43, 19.27, 25.13, 27.88, 31.73],
        "combine": [43.27, 50.12, 50.96, 51.08, 51.32],
        "color": "#5b7185",
        "marker": "D",
        "style": ":",
    },
}

RL_COMPARISON = {
    "Qwen3-8B": {
        "direct": OFFICIAL["Qwen3-8B"]["direct"],
        "combine": OFFICIAL["Qwen3-8B"]["combine"],
        "color": "#5b7185",
        "marker": "s",
        "style": "--",
    },
    "8B-RL-Zero": {
        "direct": [4.88, 18.12, 25.80, 28.92, 32.93],
        "combine": [43.15, 56.97, 58.53, 58.77, 58.77],
        "color": "#2d7053",
        "marker": "o",
        "style": "-",
    },
}


def configure() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8.2,
            "axes.titlesize": 9.2,
            "axes.labelsize": 8.5,
            "legend.fontsize": 7.6,
            "xtick.labelsize": 7.7,
            "ytick.labelsize": 7.7,
            "axes.edgecolor": "#5c6e65",
            "axes.linewidth": 0.6,
            "grid.color": "#dce5df",
            "grid.linewidth": 0.55,
            "pdf.fonttype": 42,
        }
    )


def style_axis(ax: plt.Axes, display_ticks: list[int]) -> None:
    ax.set_xscale("log")
    ax.set_xticks(display_ticks)
    ax.set_xticklabels([str(k) for k in display_ticks])
    ax.set_xlabel("Number of rollouts, $k$")
    ax.set_ylabel("Programs verified (\%)")
    ax.grid(True, which="major", alpha=0.8)
    ax.spines[["top", "right"]].set_visible(False)


def plot_lines(ax: plt.Axes, data: dict, metric: str, ks: list[int]) -> None:
    for label, values in data.items():
        ax.plot(
            ks,
            values[metric],
            label=label,
            color=values["color"],
            marker=values["marker"],
            linestyle=values["style"],
            linewidth=1.7,
            markersize=4.2,
            markeredgewidth=0.6,
            markeredgecolor="white",
        )


def official_probe() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.15, 2.65))
    plot_lines(axes[0], OFFICIAL, "direct", ROLLOUT_K)
    plot_lines(axes[1], OFFICIAL, "combine", ROLLOUT_K)
    style_axis(axes[0], ROLLOUT_K)
    style_axis(axes[1], ROLLOUT_K)
    axes[0].set_title("(a) Complete-set exploration: direct pass@$k$")
    axes[1].set_title("(b) Clause-pool utility: combine@$k$")
    axes[0].set_ylim(0, 43)
    axes[1].set_ylim(30, 61)
    axes[1].axvspan(30, 100, color="#edf4f0", alpha=0.9, zorder=0)
    axes[1].axvline(30, color="#819b8d", linewidth=0.8, linestyle="--")
    axes[1].text(
        32,
        31.4,
        "$\\leq$0.60 pp gain after $k=30$",
        color="#5c6e65",
        fontsize=7.2,
    )
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=4, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 0.86), w_pad=2.0)
    fig.savefig(OUT / "qwen_official_probe.pdf", bbox_inches="tight")
    plt.close(fig)


def rlzero_probe() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.15, 2.55))
    plot_lines(axes[0], RL_COMPARISON, "direct", ROLLOUT_K)
    plot_lines(axes[1], RL_COMPARISON, "combine", ROLLOUT_K)
    style_axis(axes[0], ROLLOUT_K)
    style_axis(axes[1], ROLLOUT_K)
    axes[0].set_title("(a) Standalone verification")
    axes[1].set_title("(b) Rollout--combine--Houdini")
    axes[0].set_ylim(0, 36)
    axes[1].set_ylim(39, 61)
    axes[1].axhline(55.41, color="#9aa8a0", linewidth=0.8, linestyle=":")
    axes[1].annotate(
        "RL-Zero@$10$ = 56.97\%\nBase@$100$ = 55.41\%",
        xy=(10, 56.97),
        xytext=(16, 48.2),
        fontsize=7.4,
        color="#2d7053",
        arrowprops={"arrowstyle": "->", "color": "#2d7053", "lw": 0.8},
    )
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 0.86), w_pad=2.0)
    fig.savefig(OUT / "qwen_rlzero_probe.pdf", bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    configure()
    official_probe()
    rlzero_probe()
