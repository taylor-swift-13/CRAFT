#!/usr/bin/env python3
"""Restyle the archived gold-augmented coverage figure without rescoring.

ROC display geometry comes from the original vector PDF. Statistical values
and Wilson intervals come from the canonical archived summary, not from
integrating the simplified display paths.
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from paper_style import FAINT, GREEN, GREEN_TINT, INK, MUTED, OCHRE, RUST, SLATE, panel_figure, panel_legend, save_panel_figure

OUT = Path(__file__).resolve().parent


def main() -> None:
    artifacts = OUT.parent.parent / "results/04_rq4_ablations/paper_summaries"
    summary = json.loads((artifacts / "negative_coverage_predictiveness.json").read_text())
    geometry = json.loads((artifacts / "negative_coverage_plot_geometry.json").read_text())
    fig, axes = panel_figure(2)
    for name, suite, color, style in (
        ("All", None, GREEN, "-"),
        ("Linear", "linear", SLATE, "--"),
        ("NLA", "NLA_lipus", OCHRE, "-."),
        ("Loopy", "Loopy", RUST, ":"),
    ):
        points = np.asarray(geometry["curves"][name])
        auc = (summary if suite is None else summary["by_suite"][suite])["macro_within_program_auroc"]
        axes[0].plot(points[:, 0], points[:, 1], color=color, linestyle=style,
                     linewidth=0.9, label=f"{name} ({auc:.3f})")
    axes[0].plot([0, 1], [0, 1], color=MUTED, linestyle="--", linewidth=0.65, alpha=0.6)
    axes[0].set(xlabel="False-positive rate", ylabel="True-positive rate",
                title="(a) ROC", xlim=(0, 1), ylim=(0, 1))
    panel_legend(fig, axes[0], columns=2)
    axes[0].set_xticks([0, 0.5, 1])
    axes[0].set_yticks([0, 0.5, 1], ['0', '.5', '1'])

    bands = summary["coverage_bands"]
    x = np.arange(len(bands))
    rates = np.asarray([b["success_rate"] for b in bands])
    lower = rates - np.asarray([b["wilson_ci95"][0] for b in bands])
    upper = np.asarray([b["wilson_ci95"][1] for b in bands]) - rates
    axes[1].bar(x, rates, width=0.72, color=GREEN_TINT, edgecolor=GREEN, linewidth=1)
    axes[1].errorbar(x, rates, yerr=np.vstack([lower, upper]), fmt="none",
                     ecolor=INK, capsize=3.5, linewidth=1)
    axes[1].set_xticks(x, [b["band"].replace("0.", ".") for b in bands], rotation=55, ha="right")
    axes[1].set(xlabel="", ylabel="Verified fraction",
                title="(b) Coverage bands", ylim=(0, 1))
    for ax in axes:
        ax.grid(axis="y", color=FAINT, linewidth=0.55, alpha=0.8)
        ax.set_axisbelow(True)
    axes[1].set_yticks([0, 0.5, 1], ['0', '.5', '1'])
    save_panel_figure(fig, OUT, 'negative_coverage_predictiveness')
    plt.close(fig)


if __name__ == "__main__":
    main()
