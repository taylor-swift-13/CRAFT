#!/usr/bin/env python3
"""Generate the RQ4 RL panels figure (fig:rl-panels).

Two panels, SFT initialization, Qwen3-8B, three-seed means, on the budget
grid k in {1, 4, 8, 16, 32}:
  (a) pass@k    before/after RL  -- expected flat (redistribution, not
                                     support expansion; yue2025rlcapacity)
  (b) compose@k before/after RL  -- expected upward shift at small budgets,
                                     with the SFT compose@32 ceiling drawn
                                     as a dotted reference line and the
                                     crossing budget k* annotated.

Data source: the canonical RL program-level pool artifact required by
paper/EXPERIMENT_PLAN.md (RQ4 evidence).  Every series below is a
placeholder (None) until the cleaned-data retraining and the new-grid
recomputation finish; fill them from the archived pool and keep the SFT
row consistent with tab:rl-before-after in sections/experiments.tex.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parent

K = [1, 4, 8, 16, 32]

# TODO(rl-rerun): replace every None with a list of five three-seed means
# (percent) aligned with K = [1, 4, 8, 16, 32], read from the canonical RL
# pool artifact.
PANELS = {
    "SFT (before RL)": {
        "pass": None,
        "combine": None,
        "color": "#5b7185",
        "marker": "s",
        "style": "--",
    },
    "SFT+RL": {
        "pass": None,
        "combine": None,
        "color": "#2d7053",
        "marker": "o",
        "style": "-",
    },
}

# Large-budget ceiling of the initialization; keep in sync with
# tab:rl-before-after (SFT 8B, before RL, compose@32).  None until the
# new-grid recomputation fills that cell.
SFT_COMBINE32 = None


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


def style_axis(ax: plt.Axes) -> None:
    ax.set_xscale("log")
    ax.set_xticks(K)
    ax.set_xticklabels([str(k) for k in K])
    ax.set_xlabel("Number of responses, $k$")
    ax.set_ylabel("Verification rate (\%)")
    ax.grid(True, which="major", alpha=0.8)
    ax.spines[["top", "right"]].set_visible(False)


def plot_lines(ax: plt.Axes, metric: str) -> None:
    for label, values in PANELS.items():
        ax.plot(
            K,
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


def crossing_budget(values: list[float], ceiling: float) -> int | None:
    """Smallest k in K whose compose@k reaches the ceiling, if any."""
    for k, v in zip(K, values):
        if v >= ceiling:
            return k
    return None


def rl_panels() -> None:
    missing = [
        f"{name}.{metric}"
        for name, values in PANELS.items()
        for metric in ("pass", "combine")
        if values[metric] is None or any(v is None for v in values[metric])
    ]
    if SFT_COMBINE32 is None:
        missing.append("SFT_COMBINE32")
    if missing:
        raise SystemExit(
            "plot_rl_panels: placeholder data, fill from the canonical RL "
            f"pool artifact on the k={K} grid first: {', '.join(missing)}"
        )

    fig, axes = plt.subplots(1, 2, figsize=(7.15, 2.55))
    plot_lines(axes[0], "pass")
    plot_lines(axes[1], "combine")
    style_axis(axes[0])
    style_axis(axes[1])
    axes[0].set_title("(a) Complete responses")
    axes[1].set_title("(b) compose@$k$ (composed responses)")

    axes[1].axhline(
        SFT_COMBINE32,
        color="#5b7185",
        linestyle=":",
        linewidth=1.1,
    )
    axes[1].text(
        1.1,
        SFT_COMBINE32 + 0.4,
        "SFT compose@32",
        fontsize=7.0,
        color="#5b7185",
    )

    k_star = crossing_budget(PANELS["SFT+RL"]["combine"], SFT_COMBINE32)
    if k_star is not None:
        axes[1].annotate(
            f"$k^{{*}}={k_star}$",
            xy=(k_star, SFT_COMBINE32),
            xytext=(k_star * 0.35, SFT_COMBINE32 - 6.0),
            fontsize=7.4,
            color="#2d7053",
            arrowprops={"arrowstyle": "->", "color": "#2d7053", "lw": 0.8},
        )

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 0.86), w_pad=2.0)
    fig.savefig(OUT / "rl_panels.pdf", bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    configure()
    rl_panels()
