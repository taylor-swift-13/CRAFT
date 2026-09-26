#!/usr/bin/env python3
"""Compare verification rates and RL gains using the paper's saved results."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from paper_style import GREEN, MUTED, PANEL_CELL_WIDTH, panel_figure, save_panel_figure

OUT = Path(__file__).resolve().parent


def main():
    data = json.loads(
        (OUT.parent.parent / "results/01_rq1_main_verification/paper_summaries/"
         "experiment_results_current.json").read_text()
    )
    k = data["k"]
    comparisons = [
        ("Bare", "Whole-rollout", "pass", "(a) Whole-rollout / Base", (0, 32)),
        ("SFT", "Whole-rollout", "pass", "(b) Whole-rollout / SFT", (30, 75)),
        ("SFT", "Full", "compose", "(c) Full / SFT", (30, 85)),
    ]
    fig, top_axes = panel_figure(3)
    fig.set_size_inches(3 * PANEL_CELL_WIDTH, 4.2)
    bottom_axes = []
    for ax in top_axes:
        pos = ax.get_position()
        ax.set_position([pos.x0, 0.57, pos.width, 0.29])
        bottom_axes.append(fig.add_axes([pos.x0, 0.14, pos.width, 0.29]))

    for ax, gain_ax, (stage, reward, metric, title, limits) in zip(
        top_axes, bottom_axes, comparisons
    ):
        before = data["stages"][stage]["Qwen3-8B"][metric]
        after = data["rewards"][stage][reward][metric]
        gains = [b - a for a, b in zip(before, after)]
        ax.plot(k, before, color=MUTED, marker="o", linestyle="--", label="Before RL")
        ax.plot(k, after, color=GREEN, marker="D", label="After RL")
        ax.set(title=title, ylim=limits, ylabel=f"{metric}@k (%)")
        for values, color, offset in [(before, MUTED, -10), (after, GREEN, 9)]:
            for index in (0, len(k) - 1):
                label_offset = offset
                if reward == "Whole-rollout" and (stage == "SFT" or index == len(k) - 1):
                    label_offset = 9 if values is before else -10
                ax.annotate(
                    f"{values[index]:.2f}", (k[index], values[index]),
                    xytext=(3 if index == 0 else -3, label_offset),
                    textcoords="offset points",
                    ha="left" if index == 0 else "right", va="center",
                    color=color, bbox=dict(facecolor="white", edgecolor="none", pad=0.2),
                )
        gain_ax.axhline(0, color=MUTED, linestyle=":", linewidth=0.8)
        gain_ax.plot(k, gains, color=GREEN, marker="D")
        gain_ax.set(
            ylim=(-19, 20), yticks=[-15, 0, 15],
            xlabel="Responses, k",
            ylabel="Gain (percentage points)" if gain_ax is bottom_axes[0] else "",
        )
        if gain_ax is not bottom_axes[0]:
            gain_ax.tick_params(labelleft=False)
        indices = (0, 2, 4) if reward == "Whole-rollout" and stage == "Bare" else (0, 4)
        for index in indices:
            gain_ax.annotate(
                f"{gains[index]:+.2f}", (k[index], gains[index]),
                xytext=(3 if index == 0 else -3,
                        -10 if gains[index] < 0 and index != len(k) - 1 else 9),
                textcoords="offset points",
                ha="left" if index == 0 else "right", va="center", color=GREEN,
                bbox=dict(facecolor="white", edgecolor="none", pad=0.2),
            )
        for panel in (ax, gain_ax):
            panel.set_xscale("log", base=2)
            panel.minorticks_off()
            panel.set_xticks(k, [str(value) for value in k])
            panel.set_xlim(0.85, 38)
            panel.grid(axis="y", alpha=0.8)

    handles, labels = top_axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2,
               bbox_to_anchor=(0.5, 0.99), frameon=False)
    save_panel_figure(fig, OUT, "reward_sharpening")
    plt.close(fig)


if __name__ == "__main__":
    main()
