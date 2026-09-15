#!/usr/bin/env python3
"""Plot current raw-results appendix probe curves and the RQ3 four-stage comparison.

Refresh the input with paper/scripts/prepare_experiment_results.py first.
"""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from paper_style import GREEN, RUST, SLATE, OCHRE, AXES_HEIGHT, PANEL_CELL_WIDTH, panel_figure, panel_legend, save_panel_figure

OUT = Path(__file__).resolve().parent


def style(ax, k):
    ax.set_xscale('log', base=2)
    ax.minorticks_off()
    ax.set_xticks(k, [str(x) for x in k])
    ax.set_xlim(.85, 38)
    ax.grid(axis='y', alpha=.8)
    ax.set_xlabel('Responses, k')
    ax.set_ylabel('Verified (%)')


def official_probe(data):
    models = ['Qwen3-1.7B', 'Qwen3-4B', 'Qwen3-8B',
              'Qwen3-14B', 'Qwen3-30B-A3B', 'Llama 3.1-8B']
    fig, axes = panel_figure(6)
    width, height = 3 * PANEL_CELL_WIDTH, 3.60
    fig.set_size_inches(width, height)
    for i, (ax, model) in enumerate(zip(axes, models)):
        pos = ax.get_position()
        ax.set_position([((i % 3) * PANEL_CELL_WIDTH + .355) / width,
                         (2.05 if i < 3 else .40) / height,
                         pos.width * 2, AXES_HEIGHT / height])
        r = data['stages']['Bare'][model]
        for metric, color, marker, ls in [('pass', RUST, 'o', '-'), ('compose', GREEN, 's', '--')]:
            ax.plot(data['k'], r[metric], label=metric+'@k', color=color,
                    marker=marker, linestyle=ls, markersize=3.8)
        style(ax, data['k'])
        ax.set(ylim=(0, 72))
        ax.set_yticks([0, 20, 40, 60])
        ax.set_title(model, fontsize=8.8)
    panel_legend(fig, axes[0], columns=2)
    save_panel_figure(fig, OUT, 'base_model_probe_all')
    plt.close(fig)


def training_stages(data, *, row_layout=False, stem="training_stages"):
    import numpy as np
    from scipy.interpolate import PchipInterpolator, CubicHermiteSpline
    from matplotlib.lines import Line2D

    fig, axes = panel_figure(2)
    height = 2.10 if row_layout else 1.78
    bottom = .32 if row_layout else .29
    fig.set_size_inches(fig.get_size_inches()[0], height)
    for i, ax in enumerate(axes):
        pos = ax.get_position()
        # Reduce the seam while preserving each panel's physical dimensions.
        ax.set_position([pos.x0 + .01, bottom/height,
                         pos.width, AXES_HEIGHT/height])
    k = np.asarray(data['k'])
    for ax, title in zip(axes, ['(a) Bare → RL', '(b) SFT → RL']):
        style(ax, k)
        ax.set_ylim(35, 83)
        ax.set_yticks([40, 60, 80])
        ax.set_ylabel('compose@k (%)')
        ax.set_xlabel('Responses, k', labelpad=0)
        ax.set_title(title, pad=3)
    # Keep a compact seam while preserving the common y scale and measured values.
    transform = fig.transFigure.inverted()
    start = transform.transform(axes[0].transData.transform((1, 40)))[0]
    end = transform.transform(axes[0].transData.transform((32, 40)))[0]
    next_start = transform.transform(axes[1].transData.transform((1, 40)))[0]
    seam = .31*(end-start)
    pos = axes[1].get_position()
    axes[1].set_position([pos.x0-(next_start-end-seam), pos.y0, pos.width, pos.height])
    axes[1].set_ylabel('')
    axes[1].spines['left'].set_visible(False)
    axes[1].tick_params(axis='y', left=False, labelleft=False)

    # Each solid curve passes through every observation in its own panel.
    # Dashed tangent bridges join interior curve segments, not the k=32/1
    # endpoints.  They illustrate the intervention and are not measurements.
    to_fig = fig.transFigure.inverted()
    handles = []
    for stages, label, color, marker in [
            (('Bare', 'SFT'), 'Before RL', SLATE, 'o'),
            (('RL', 'SFT+RL'), 'After RL', GREEN, 'D')]:
        panel_points, curves = [], []
        for ax, stage in zip(axes, stages):
            y = data['stages'][stage]['Qwen3-8B']['compose']
            points = to_fig.transform(ax.transData.transform(np.column_stack([k, y])))
            curve = PchipInterpolator(points[:, 0], points[:, 1])
            np.testing.assert_allclose(curve(points[:, 0]), points[:, 1], atol=1e-12)
            x = np.unique(np.r_[np.linspace(points[0, 0], points[-1, 0], 160), points[:, 0]])
            fig.add_artist(Line2D(x, curve(x), transform=fig.transFigure,
                                 color=color, linewidth=1.4, solid_capstyle='round', zorder=3))
            ax.plot(k, y, linestyle='none', marker=marker, markersize=3.8,
                    color=color, zorder=4)
            panel_points.append(points)
            curves.append(curve)

        # Join the left curve at k=4 to the right curve at k=8.  The bridge
        # matches both tangents and has a decreasing slope throughout, making
        # one smooth concave outline with the adjoining solid segments.
        left = panel_points[0][1]
        right = panel_points[1][2]
        derivatives = [curves[0].derivative()(left[0]), curves[1].derivative()(right[0])]
        bridge = CubicHermiteSpline([left[0], right[0]], [left[1], right[1]], derivatives)
        x = np.linspace(left[0], right[0], 240)
        assert np.all(bridge.derivative()(x) > 0)
        assert np.all(bridge.derivative(2)(x) <= 1e-10), 'Bridge must remain concave.'
        fig.add_artist(Line2D(x, bridge(x), transform=fig.transFigure,
                             color=color, linewidth=1.4, linestyle=(0, (2.4, 1.8)),
                             dash_capstyle='round', zorder=2))
        handles.append(Line2D([], [], color=color, marker=marker,
                              markersize=3.8, lw=1.4, label=label))

    # Two schematic intervention arrows, rather than numerical delta labels.
    # RL: upper-left pull toward useful candidates at a smaller sample budget.
    axes[0].annotate('', xy=(2.5, 53), xytext=(9, 43),
                     arrowprops=dict(arrowstyle='-|>', color=GREEN, lw=1.35,
                                     mutation_scale=10, connectionstyle='arc3,rad=-.12'))
    axes[0].text(6.3, 46, 'RL', fontsize=8.8, color=GREEN,
                 ha='left', va='bottom')
    # SFT: compress the response budget from large k to small k.
    axes[1].annotate('', xy=(1.5, 51), xytext=(25, 51),
                     arrowprops=dict(arrowstyle='-|>', color=SLATE, lw=1.35,
                                     mutation_scale=10))
    axes[1].text(6, 53.4, 'SFT', fontsize=8.8, color=SLATE,
                 ha='center', va='bottom')
    fig.legend(handles=handles, loc='upper center', ncol=2,
               bbox_to_anchor=(.55, 1.0 if row_layout else 1.015), borderaxespad=0,
               columnspacing=1.0, handletextpad=.5)
    save_panel_figure(fig, OUT, stem)
    plt.close(fig)


def main():
    data = json.loads((OUT.parent.parent/'results/01_rq1_main_verification/paper_summaries/experiment_results_current.json').read_text())
    official_probe(data)
    training_stages(data)


if __name__ == '__main__':
    main()
