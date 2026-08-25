#!/usr/bin/env python3
"""Generate the base-checkpoint exploration and RL-Zero figures.

Budget grid: k in {1, 4, 8, 16, 32}.  Only the k=1 entries below are
measured; every other entry is a placeholder (None) until the new-grid
recomputation finishes (compose@k by prefix-subset composition of the
archived pools, pass@k by the unbiased estimator over the archived
per-rollout verdicts).  The guard below refuses to render a figure from
placeholder data.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


OUT = Path(__file__).resolve().parent

RQ1_K = [1, 4, 8, 16, 32]
RL_K = [1, 4, 8, 16, 32]

# k=1 values are measured; None marks the k in {4, 8, 16, 32} slots that
# still await the new-grid recomputation.
OFFICIAL = {
    "Qwen3-0.6B": {
        "direct": [0.58, None, None, None, None],
        "combine": [9.50, None, None, None, None],
        "color": "#b47a5f",
        "marker": "P",
        "style": "-",
    },
    "Qwen3-4B": {
        "direct": [3.49, None, None, None, None],
        "combine": [23.08, None, None, None, None],
        "color": "#9d6652",
        "marker": "o",
        "style": "-",
    },
    "Qwen3-8B": {
        "direct": [3.55, None, None, None, None],
        "combine": [26.32, None, None, None, None],
        "color": "#4f8a6b",
        "marker": "s",
        "style": "--",
    },
    "Qwen3-14B": {
        "direct": [4.99, None, None, None, None],
        "combine": [30.17, None, None, None, None],
        "color": "#2d7053",
        "marker": "^",
        "style": "-.",
    },
    "Qwen3-30B-A3B": {
        "direct": [5.00, None, None, None, None],
        "combine": [28.73, None, None, None, None],
        "color": "#5b7185",
        "marker": "D",
        "style": ":",
    },
    "Llama 3.1 8B": {
        "direct": [0.48, None, None, None, None],
        "combine": [18.03, None, None, None, None],
        "color": "#75618f",
        "marker": "v",
        "style": "-",
    },
}

RL_COMPARISON = {
    "Qwen3-8B": {
        "direct": [9.30, None, None, None, None],
        "combine": [41.23, None, None, None, None],
        "color": "#5b7185",
        "marker": "s",
        "style": "--",
    },
    "8B-RL-Zero": {
        "direct": [4.88, None, None, None, None],
        "combine": [43.15, None, None, None, None],
        "color": "#2d7053",
        "marker": "o",
        "style": "-",
    },
}


def require_complete(data: dict, name: str, ks: list[int]) -> None:
    """SystemExit on placeholder data so stale plots are never rendered."""
    missing = [
        f"{label}.{metric}"
        for label, values in data.items()
        for metric in ("direct", "combine")
        if values[metric] is None or any(v is None for v in values[metric])
    ]
    if missing:
        raise SystemExit(
            f"plot_qwen_probes ({name}): placeholder data on the k={ks} "
            "grid; fill from the new-grid recomputation first: "
            + ", ".join(missing)
        )


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
    require_complete(OFFICIAL, "official_probe", RQ1_K)
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
            label="compose@$k$",
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
        ax.set_ylim(0, 44)

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
    require_complete(RL_COMPARISON, "rlzero_probe", RL_K)
    fig, axes = plt.subplots(1, 2, figsize=(7.15, 2.55))
    plot_lines(axes[0], RL_COMPARISON, "direct", RL_K)
    plot_lines(axes[1], RL_COMPARISON, "combine", RL_K)
    style_axis(axes[0], RL_K)
    style_axis(axes[1], RL_K)
    axes[0].set_title("(a) Complete responses")
    axes[1].set_title("(b) compose@$k$ (composed responses)")
    axes[0].set_ylim(0, 36)
    axes[1].set_ylim(39, 61)
    # TODO(regrid): re-add the Base@8 vs RL-Zero@8 annotation once the
    # recomputed k=8 values are filled in above.
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 0.86), w_pad=2.0)
    fig.savefig(OUT / "qwen_rlzero_probe.pdf", bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    configure()
    official_probe()
    rlzero_probe()
