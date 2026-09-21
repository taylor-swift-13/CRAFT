#!/usr/bin/env python3
"""Plot target visibility for the fixed Qwen3-8B SFT+RL checkpoint."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from paper_style import GREEN, RUST, panel_figure, panel_legend, save_panel_figure


def main() -> None:
    output = Path(__file__).resolve().parent
    data = json.loads((output.parent.parent / "results/04_rq4_ablations/paper_summaries/target_visibility.json").read_text())
    k = data["k"]
    hidden = data["target_hidden"]
    visible = data["target_visible"]
    visible_compose = [100 * n / data["programs"] for n in visible["compose_counts"]]

    fig, axes = panel_figure(2)
    panels = [
        ("(a) compose@k", hidden["compose_percent"], visible_compose,
         (65, 85), [65, 70, 75, 80, 85]),
        ("(b) pass@k", hidden["pass_percent"], visible["pass_percent"],
         (25, 65), [25, 35, 45, 55, 65]),
    ]
    for ax, (title, hidden_values, visible_values, limits, ticks) in zip(axes, panels):
        for label, values, color, marker, linestyle in [
            ("Target-hidden", hidden_values, GREEN, "D", "-"),
            ("Target-visible", visible_values, RUST, "^", "--"),
        ]:
            ax.plot(k, values, label=label, color=color, marker=marker,
                    linestyle=linestyle, linewidth=1.4, markersize=3.8,
                    markeredgewidth=0.5, markeredgecolor="white")
            endpoint_y = -9 if title == "(b) pass@k" and label == "Target-hidden" else 6
            ax.annotate(f"{values[-1]:.2f}", (k[-1], values[-1]),
                        xytext=(-3, endpoint_y), textcoords="offset points",
                        va="center", ha="right", color=color, fontsize=8.2)
        ax.set_title(title)
        ax.set_xscale("log", base=2)
        ax.set_xlim(0.85, 55)
        ax.set_xticks(k, labels=[str(value) for value in k])
        ax.minorticks_off()
        ax.set_ylim(*limits)
        ax.set_yticks(ticks)
        ax.set_xlabel("Responses, k")
        ax.set_ylabel("Verified (%)")
        ax.grid(axis="y", alpha=0.8)
        ax.set_axisbelow(True)
        ax.spines[["top", "right"]].set_visible(False)

    panel_legend(fig, axes[0], columns=2)
    save_panel_figure(fig, output, 'target_visibility')
    plt.close(fig)


if __name__ == "__main__":
    main()
