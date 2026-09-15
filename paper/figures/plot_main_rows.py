#!/usr/bin/env python3
"""Render aligned main-text rows and all six Bare probes for the unified manuscript.

Usage: python3 paper/figures/plot_main_rows.py paper
Data always come from the selected paper's own raw-results appendix export.
"""
import argparse
import json
from pathlib import Path

import paper_style as style
import plot_qwen_probes as probes
import plot_reward_ablation as rewards
import plot_sft_composition as composition
import plot_tool_pareto as tools


def row_panel_figure(count):
    fig, axes = style.panel_figure(count)
    height = 2.10
    fig.set_size_inches(fig.get_size_inches()[0], height)
    for ax in axes:
        pos = ax.get_position()
        ax.set_position([pos.x0, .32 / height, pos.width, style.AXES_HEIGHT / height])
    return fig, axes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paper', type=Path)
    root = parser.parse_args().paper.resolve()
    output = root / 'figures'
    data = json.loads((root.parent / 'results/01_rq1_main_verification/paper_summaries/experiment_results_current.json').read_text())
    for module in (probes, rewards, composition, tools):
        module.OUT = output

    probes.official_probe(data)
    probes.training_stages(data, row_layout=True, stem='training_stages_row')
    # Retain an identically styled, standalone pass plot in the appendix.
    composition.main()

    def save_row(fig, out, stem):
        if stem in ('tool_pareto', 'reward_ablation', 'sft_composition_compose'):
            style.save_panel_figure(fig, out, stem + '_row')

    for module in (tools, composition, rewards):
        module.panel_figure = row_panel_figure
        module.save_panel_figure = save_row
        module.main()
    print(root.name, 'rendered two three-panel rows and six Bare probe curves')


if __name__ == '__main__':
    main()
