#!/usr/bin/env python3
"""Plot RQ2 verification rates against token use and end-to-end time."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from paper_style import FAINT, GREEN, INK, MUTED, RUST, panel_figure, panel_legend, save_panel_figure

OUT = Path(__file__).resolve().parent

# (mean total tokens / called task, verified %, mean seconds / task).
# Source: appendix.tex, tab:tools-complete, reasoning-disabled rows.  These
# Verification rates and costs use each method's full supported subset:
# 832 tasks except for Clause2Inv (316 Linear) and LaM4Inv (366 Linear+NLA).
BASELINES = {
    "AutoSpec": (2216.11, 42.19, 27.34),
    "SESpec": (22081.61, 29.69, 68.50),
    "Clause2Inv": (1461.80, 20.89, 18.37),
    "Loopy": (14513.37, 18.75, 147.16),
    "LORIS": (47816.27, 42.55, 138.17),
    "LaM4Inv": (1470.80, 19.40, 14.70),
}
NAIVE = (225.95, 16.47, 2.98)
DAIKON = (0, 20.91, 4.89)
# GPT-5-nano CRAFT times combine archived generation latency with measured
# prefix filtering and verification; see
# results/02_rq2_tool_comparison/paper_summaries/tool_compose_cost.json.
OURS = {
    "@1": (1136.82, 33.89, 24.52),
    "@4": (1905.10, 49.04, 46.40),
    "@8": (2929.47, 55.53, 69.99),
}
# Selected Qwen3-8B + SFT + RL verification rates, paired with the recorded
# local cost-run token and end-to-end-time measurements.
TRAINED = {
    "@1": (1348.23, 69.23, 28.22),
    "@4": (2053.71, 75.24, 33.53),
    "@8": (2993.44, 76.80, 40.90),
}


def plot_panel(ax: plt.Axes, cost_index: int) -> None:
    is_time = cost_index == 2
    baseline_labels = {
        "AutoSpec": ((-5, 5), "right") if is_time else ((14, 0), "left"),
        "SESpec": ((-2, 6), "left") if is_time else ((-3, -5), "right"),
        "Clause2Inv": ((-3, 17), "right") if is_time else ((0, -15), "center"),
        "Loopy": ((-2, -10), "center") if is_time else ((-2, -12), "center"),
        "LORIS": ((0, -13), "center") if is_time else ((-2, 6), "right"),
        "LaM4Inv": ((0, -15), "center") if is_time else ((-3, 6), "right"),
    }
    for name, value in BASELINES.items():
        point = (value[cost_index], value[1])
        ax.scatter(*point, s=14, marker="o", color=MUTED,
                   edgecolor="white", linewidth=0.5, zorder=3)
        offset, alignment = baseline_labels[name]
        ax.annotate(name, point, xytext=offset, ha=alignment,
                    textcoords="offset points", color=INK, fontsize=8.2)

    for name, value in [("Naive", NAIVE), *([("Daikon", DAIKON)] if is_time else [])]:
        point = (value[cost_index], value[1])
        ax.scatter(*point, s=14, marker="o", color=MUTED,
                   edgecolor="white", linewidth=0.5, zorder=3)
        point_offset = (4, 3) if is_time and name == "Naive" else (5, 6)
        if not is_time:
            point_offset = (3, -3)
        if is_time and name == "Daikon":
            point_offset = (-3, 7)
        ax.annotate(name, point, xytext=point_offset,
                    textcoords="offset points",
                    ha="right" if is_time and name == "Daikon" else "left",
                    color=INK, fontsize=8.2)

    for data, label, color, marker, style in (
        (OURS, "CRAFT (GPT-5-nano)", GREEN, "D", "-"),
        (TRAINED, "CRAFT (trained)", RUST, "^", "--"),
    ):
        xs = [value[cost_index] for value in data.values()]
        ys = [value[1] for value in data.values()]
        ax.plot(xs, ys, color=color, marker=marker, markersize=3.8,
                markeredgecolor="white", markeredgewidth=0.5,
                linewidth=1.4, linestyle=style, label=label, zorder=4)
        for budget, value in data.items():
            offset, alignment = (5, -3), "left"
            if not is_time and data is OURS and budget == "@1":
                offset, alignment = (-5, 0), "right"
            if is_time and budget == "@4":
                offset, alignment = (0, 11), "center"
            if data is TRAINED:
                offset, alignment = {
                    "@1": ((-4, -9), "right"),
                    "@4": ((-5, 10), "center"),
                    "@8": ((4, 0), "left"),
                }[budget]
            ax.annotate(budget, (value[cost_index], value[1]), xytext=offset,
                        textcoords="offset points", va="center", ha=alignment,
                        color=color, fontsize=8.2, fontweight="bold")

    ax.set_xscale("log")
    if is_time:
        ax.set_xlim(2.2, 220)
        ax.set_xticks([3, 30, 200])
        ax.set_xticklabels(["3", "30", "200"])
        ax.get_xticklabels()[-1].set_ha("right")
        ax.set_xlabel("Time / task (s)", labelpad=1)
        ax.set_title("(b) Time", loc="left", pad=5)
    else:
        ax.set_xlim(170, 65000)
        ax.set_xticks([200, 2000, 20000])
        ax.set_xticklabels(["0.2k", "2k", "20k"])
        ax.set_xlabel("Tokens / called task", labelpad=1)
        ax.set_title("(a) Tokens", loc="left", pad=5)
    ax.set_ylim(0, 90)
    ax.set_yticks([0, 20, 40, 60, 80])
    ax.grid(axis="y", color=FAINT, linewidth=0.55, alpha=0.9)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(color="#92A39A", width=0.6)


def main() -> None:
    for cost_index, filename, title in (
        (0, "tool_pareto", "Token use"),
        (2, "tool_pareto_time", "End-to-end time"),
    ):
        fig, (ax,) = panel_figure(1)
        plot_panel(ax, cost_index)
        ax.set_title(title, loc="left", pad=5)
        ax.set_ylabel("Verified (%)", labelpad=1)
        panel_legend(fig, ax, columns=1)
        save_panel_figure(fig, OUT, filename)
        plt.close(fig)



if __name__ == "__main__":
    main()
