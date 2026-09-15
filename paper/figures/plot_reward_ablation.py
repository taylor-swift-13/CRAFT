#!/usr/bin/env python3
"""Plot the RQ4 reward ablation using the current raw-results and independent-experiments appendices data."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from paper_style import GREEN, MUTED, OCHRE, RUST, SLATE, panel_figure, panel_legend, save_panel_figure

OUT = Path(__file__).resolve().parent


def main():
    data = json.loads((OUT.parent.parent/'results/01_rq1_main_verification/paper_summaries/experiment_results_current.json').read_text())
    rewards = data['rewards']['Bare']
    default = data['default_reward']
    colors = {'Binary': RUST, 'Whole-rollout': OCHRE, 'Clause-decomposed': SLATE, 'Full': GREEN}
    colors[default] = GREEN
    markers = {'Binary': 'o', 'Whole-rollout': 's', 'Clause-decomposed': '^', 'Full': 'D'}
    fig, axes = panel_figure(2)
    for ax, metric in zip(axes, ['pass', 'compose']):
        ax.plot(data['k'], data['stages']['Bare']['Qwen3-8B'][metric],
                color=MUTED, marker='x', linestyle=':', label='Bare reference', linewidth=1.2)
        for reward, values in rewards.items():
            label = reward+(' (default)' if reward==default else '')
            ax.plot(data['k'], values[metric], color=colors[reward], marker=markers[reward],
                    linestyle='--' if reward=='Whole-rollout' else '-',
                    label=label, markersize=3.8, linewidth=1.4)
        ax.set_xscale('log', base=2)
        ax.minorticks_off()
        ax.set_xticks(data['k'], [str(x) for x in data['k']])
        ax.set(xlim=(.85,38), ylim=(0,30 if metric=='pass' else (75 if default=='Full' else 70)),
               xlabel='Responses, k', ylabel='Verified (%)')
        ax.set_yticks([0,10,20,30] if metric=='pass' else [0,20,40,60])
        ax.set_title('(a) pass@k' if metric=='pass' else '(b) compose@k')
        ax.grid(axis='y', alpha=.8)
    panel_legend(fig, axes[0], columns=2)
    save_panel_figure(fig, OUT, 'reward_ablation')
    plt.close(fig)


if __name__=='__main__':
    main()
