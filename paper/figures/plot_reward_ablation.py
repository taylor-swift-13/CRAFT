#!/usr/bin/env python3
"""Plot the RQ4 reward ablation using the current raw-results and independent-experiments appendices data."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from paper_style import GREEN, MUTED, OCHRE, ORANGE, RUST, panel_figure, panel_legend, save_panel_figure

OUT = Path(__file__).resolve().parent


def main():
    data = json.loads((OUT.parent.parent/'results/01_rq1_main_verification/paper_summaries/experiment_results_current.json').read_text())
    rewards = data['rewards']['Bare']
    default = data['default_reward']
    colors = {'Binary': RUST, 'Whole-rollout': OCHRE, 'Clause-decomposed': ORANGE, 'Full': GREEN}
    colors[default] = GREEN
    markers = {'Binary': 'o', 'Whole-rollout': 's', 'Clause-decomposed': '^', 'Full': 'D'}
    fig, axes = panel_figure(2)
    for ax, metric in zip(axes, ['compose', 'pass']):
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
        ax.set_title('(a) compose@k' if metric=='compose' else '(b) pass@k')
        ax.grid(axis='y', alpha=.8)
        # Label the k=32 values discussed in the main text. Stagger the two
        # high compose curves so both values remain legible at column width.
        labels = (
            [('Binary', (-4, 8)), ('Whole-rollout', (-4, 8)),
             ('Bare', (-4, -12)), ('Clause-decomposed', (-4, -10)),
             ('Full', (-4, 10))]
            if metric == 'compose' else
            [('Bare', (-4, 10)), ('Clause-decomposed', (-4, -10))]
        )
        for name, offset in labels:
            values = (data['stages']['Bare']['Qwen3-8B'][metric]
                      if name == 'Bare' else rewards[name][metric])
            color = MUTED if name == 'Bare' else colors[name]
            ax.annotate(f'{values[-1]:.2f}', (data['k'][-1], values[-1]),
                        xytext=offset, textcoords='offset points',
                        ha='right', va='center', color=color, fontsize=8.2,
                        bbox=dict(facecolor='white', edgecolor='none', pad=0.25,
                                  alpha=0.92), zorder=5)
    panel_legend(fig, axes[0], columns=2)
    save_panel_figure(fig, OUT, 'reward_ablation')
    plt.close(fig)


if __name__=='__main__':
    main()
