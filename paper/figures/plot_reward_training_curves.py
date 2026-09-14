#!/usr/bin/env python3
"""Plot the supplied per-step reward records without rescaling objectives."""
import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import mean

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from paper_style import GREEN, OCHRE, RUST, SLATE, panel_figure, panel_legend, save_panel_figure

OUT = Path(__file__).resolve().parent
SOURCE = OUT.parent / 'artifacts/reward_curves_step1-282.csv'
WINDOW = 15
SERIES = (
    ('binary', 'Binary', RUST, ':'),
    ('wc', 'Whole-rollout', OCHRE, '--'),
    ('base', 'Clause-decomposed', SLATE, '-.'),
    ('full', 'Full (default)', GREEN, '-'),
)


def main():
    records = {'zero': [], 'rft': []}
    with SOURCE.open(newline='') as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == ['init', 'step', 'full', 'base', 'wc', 'binary']
        for row in reader:
            records[row['init']].append({
                'step': int(row['step']),
                **{key: float(row[key]) for key, *_ in SERIES},
            })
    summary = {
        'source': SOURCE.name,
        'sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        'metric': 'Per-step mean training reward, confirmed by the author; no cross-objective normalization',
        'initialization_mapping': {'zero': 'Bare', 'rft': 'SFT'},
        'reward_mapping': {key: label for key, label, *_ in SERIES},
        'smoothing': {'kind': 'trailing arithmetic mean', 'window_steps': WINDOW, 'minimum_steps': 1},
        'summary_windows': {'first': [1, 20], 'last': [263, 282]},
        'series': {},
    }
    fig, axes = panel_figure(2)
    for ax, (init, title) in zip(axes, [('zero', '(a) Bare'), ('rft', '(b) SFT')]):
        rows = sorted(records[init], key=lambda row: row['step'])
        steps = [row['step'] for row in rows]
        assert steps == list(range(1, 283)), (init, 'missing or duplicated step')
        epochs = [2 * step / steps[-1] for step in steps]
        summary['series'][init] = {}
        for key, label, color, style in SERIES:
            values = [row[key] for row in rows]
            assert all(math.isfinite(value) for value in values)
            smoothed = [mean(values[max(0, i-WINDOW+1):i+1]) for i in range(len(values))]
            ax.plot(epochs, values, color=color, linewidth=.35, alpha=.08, linestyle='-', zorder=1)
            ax.plot(epochs, smoothed, color=color, linewidth=.65, linestyle=style, label=label, zorder=2)
            summary['series'][init][key] = {
                'count': len(values), 'first_20_mean': mean(values[:20]),
                'last_20_mean': mean(values[-20:]), 'minimum': min(values),
                'maximum': max(values), 'last': values[-1],
            }
        ax.set(xlim=(0, 2), ylim=(0, 1.05), xlabel='RL epoch', ylabel='Mean reward')
        ax.set_xticks([0, 1, 2])
        ax.get_xticklabels()[-1].set_horizontalalignment('right')
        ax.set_yticks([0, .5, 1])
        ax.set_title(title)
        ax.grid(axis='y', alpha=.8)
    panel_legend(fig, axes[0], columns=2)
    save_panel_figure(fig, OUT, 'reward_training_curves', normalize_linewidth=False)
    plt.close(fig)
    (OUT.parent / 'artifacts/reward_training_curves_summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print('Plotted eight curves: 564 rows, 2,256 reward observations; all steps 1–282 present.')


if __name__ == '__main__':
    main()
