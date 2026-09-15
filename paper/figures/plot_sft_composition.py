#!/usr/bin/env python3
"""Plot Qwen3-8B Bare/SFT(single)/SFT curves from raw-results appendix data."""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from paper_style import GREEN, OCHRE, SLATE, panel_figure, panel_legend, save_panel_figure


OUT = Path(__file__).resolve().parent
STYLES = (
    ("Qwen3-8B", "Bare", SLATE, "o", ":"),
    ("Qwen3-8B + SFT (single)", "SFT (single)", OCHRE, "s", "--"),
    ("Qwen3-8B + SFT", "SFT", GREEN, "D", "-"),
)


def main():
    data = json.loads((OUT.parent.parent / "results/03_rq3_training_stages/paper_summaries/sft_composition_qwen3_8b.json").read_text())
    for metric in ("compose", "pass"):
        fig, (ax,) = panel_figure(1)
        for name, label, color, marker, linestyle in STYLES:
            values = data["series"][name][metric]
            ax.plot(data["k"], values, label=label, color=color,
                    marker=marker, linestyle=linestyle, linewidth=1.4,
                    markersize=3.8, markeredgewidth=0.6)
            for i in (0, -1):
                ax.annotate(f"{values[i]:.2f}", (data["k"][i], values[i]),
                            xytext=(3 if i == 0 else -3, 6),
                            textcoords="offset points", va="center",
                            ha="left" if i == 0 else "right",
                            fontsize=8.2, color=color)
        ax.set_xscale("log", base=2)
        ax.minorticks_off()
        ax.set_xticks(data["k"], [str(k) for k in data["k"]])
        ax.set(xlim=(0.52, 62), ylim=(0, 90),
               xlabel="Responses, k",
               ylabel=f"{metric}@k (%)")
        ax.set_yticks(range(0, 81, 20))
        ax.set_title("Qwen3-8B")
        ax.grid(axis="y", alpha=0.8)
        panel_legend(fig, ax, columns=1)
        save_panel_figure(fig, OUT, f'sft_composition_{metric}')
        plt.close(fig)


if __name__ == "__main__":
    main()
