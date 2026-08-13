#!/usr/bin/env python3
"""Generate the base-checkpoint exploration and RL-Zero figures."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


OUT = Path(__file__).resolve().parent

RQ1_K = [1, 10, 30, 50, 100]
RL_K = [1, 10, 30, 50, 100]

OFFICIAL = {
    "Qwen3-0.6B": {
        "direct": [0.70, 4.09, 7.12, 9.00, 12.26],
        "combine": [6.61, 24.16, 25.00, 25.00, 25.00],
        "color": "#b47a5f",
        "marker": "P",
        "style": "-",
    },
    "Qwen3-4B": {
        "direct": [2.53, 7.56, 11.21, 13.10, 15.62],
        "combine": [33.89, 45.31, 47.24, 47.60, 47.84],
        "color": "#9d6652",
        "marker": "o",
        "style": "-",
    },
    "Qwen3-8B": {
        "direct": [9.30, 22.87, 28.30, 30.47, 33.29],
        "combine": [41.23, 52.76, 56.37, 57.09, 58.05],
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
    "Llama 3.1 8B": {
        "direct": [0.14, 1.23, 3.0345, 4.46, 7.33],
        "combine": [16.59, 30.41, 34.13, 34.38, 34.38],
        "color": "#75618f",
        "marker": "v",
        "style": "-",
    },
}

RL_COMPARISON = {
    "Qwen3-8B": {
        "direct": [9.30, 22.87, 28.30, 30.47, 33.29],
        "combine": [41.23, 52.76, 56.37, 57.09, 58.05],
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
    ax.set_xlabel("Number of responses, $k$")
    ax.set_ylabel("Verification rate (\%)")
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
    fig, axes = plt.subplots(2, 3, figsize=(7.2, 3.75), sharex=True, sharey=True)
    panel_names = ["(a)", "(b)", "(c)", "(d)", "(e)", "(f)"]
    for panel, (model, values) in enumerate(OFFICIAL.items()):
        ax = axes.flat[panel]
        ax.plot(
            RQ1_K,
            values["direct"],
            label="pass@$k$",
            color="#9d6652",
            marker="o",
            linestyle="-",
            linewidth=1.6,
            markersize=3.8,
            markeredgewidth=0.5,
            markeredgecolor="white",
        )
        ax.plot(
            RQ1_K,
            values["combine"],
            label="combine@$k$",
            color="#2d7053",
            marker="s",
            linestyle="--",
            linewidth=1.6,
            markersize=3.8,
            markeredgewidth=0.5,
            markeredgecolor="white",
        )
        ax.set_xscale("log")
        ax.set_xticks(RQ1_K)
        ax.set_xticklabels([str(k) for k in RQ1_K])
        ax.set_xlabel("Number of responses, $k$")
        ax.set_ylabel("Verification rate (\%)")
        ax.grid(True, which="major", alpha=0.8)
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_title(f"{panel_names[panel]} {model}")
        ax.set_ylim(0, 62)

    # Shared labels keep the six model-specific panels compact.
    axes[0, 0].set_xlabel("")
    axes[0, 1].set_xlabel("")
    axes[0, 2].set_xlabel("")
    axes[0, 1].set_ylabel("")
    axes[0, 2].set_ylabel("")
    axes[1, 1].set_ylabel("")
    axes[1, 2].set_ylabel("")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 0.90), h_pad=0.9, w_pad=1.0)
    fig.savefig(OUT / "base_model_probe.pdf", bbox_inches="tight")
    plt.close(fig)


def rlzero_probe() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.15, 2.55))
    plot_lines(axes[0], RL_COMPARISON, "direct", RL_K)
    plot_lines(axes[1], RL_COMPARISON, "combine", RL_K)
    style_axis(axes[0], RL_K)
    style_axis(axes[1], RL_K)
    axes[0].set_title("(a) Complete responses")
    axes[1].set_title("(b) Combined responses")
    axes[0].set_ylim(0, 36)
    axes[1].set_ylim(39, 61)
    axes[1].annotate(
        "Base@$10$ = 52.76\%\nRL-Zero@$10$ = 56.97\%",
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
