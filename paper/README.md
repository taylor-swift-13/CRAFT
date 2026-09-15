# Unified CRAFT manuscript

`paper/` is the sole maintained manuscript. The removed no-Shapley draft
remains recoverable from Git history. Pre-merge drafts are preserved locally under
`.paper_archive/20260912_before_merge/` at the repository root.

The writing and figure style follow the former no-Shapley draft: compositional
inference, composed-target SFT, and inference-aligned RL form the main narrative.
Full is the default reward, as confirmed by the author:

```
Full = Clause-decomposed + 0.3 * Shapley
```

There is no separate redundancy or overflow penalty. Shapley receives a short
method definition and RQ4 analysis, with all definitions, theory, and proofs collected in
Appendix A. Shapley equivalence is in A.6.1, reward-collapse analysis in A.7.1,
and composition properties and proofs in A.10. The empirical Full comparisons
are in Appendix K.9; the independent Full sampler experiment is in K.5.
Appendix J contains complete results for the training configurations, including
Bare reference models; Appendix K contains all supplementary experiments.
K.3 covers the four reward ablations, including Full versus Clause-decomposed,
and training reward curves. K.9 contains the Full versus Clause-decomposed comparison, its explanation,
and the supplied policy entropy and batch reward-ratio diagnostics. These diagnostics do not directly
measure per-program group reward collapse. The manuscript reports 2 RL epochs;
the five-epoch measurements remain archived in
`results/04_rq4_ablations/paper_summaries/full_base_epoch_deltas_20260912.json`
and are not relabeled or plotted
as two-epoch results. Entropy source measurements and original retention ratios
are retained in
`results/04_rq4_ablations/paper_summaries/shapley_exploration_diagnostics_20260912.json`.

## Current Full evaluation

Qwen3-8B + SFT + RL (Full) uses **69.23% compose@1** throughout the
main results and reward comparisons; Clause-decomposed uses **67.91%**.
The complete corresponding Full pass/compose row is used consistently.
The comparison is not labeled as paired retraining. The superseded evaluation
is preserved in
`results/01_rq1_main_verification/paper_summaries/full_superseded_evaluation_20260912.tex`
and the
original reports, and is no longer a separate manuscript table.
The author also confirmed that Qwen3-4B and Llama post-SFT models use Full;
`results/04_rq4_ablations/paper_summaries/reward_label_mapping_current.json`
supersedes the earlier mapping.

## Rebuild

From the repository root:

```bash
python3 paper/scripts/prepare_experiment_results.py paper
python3 paper/figures/plot_main_rows.py paper
python3 paper/figures/plot_reward_training_curves.py
make -C paper
```

Raw evaluation reports remain unchanged. Current author corrections and
checkpoint provenance are stored in
`results/04_rq4_ablations/paper_summaries/reward_definition_confirmation_20260912.json`
and
`results/01_rq1_main_verification/paper_summaries/result_alignment_20260911.json`.

## Reward training curves

Appendix K.3.1 includes the supplied step-1–282 reward records for both Bare
and SFT initializations and all four reward configurations. The original CSV
is preserved byte-for-byte in
`results/04_rq4_ablations/paper_summaries/reward_curves_step1-282.csv`.
`figures/plot_reward_training_curves.py` validates all steps, plots author-confirmed per-step mean rewards
and trailing 15-step means using the shared figure style, with the horizontal
axis expressed as RL epochs from 0 to 2, and exports source
hash and first/last-20-step statistics to
`results/04_rq4_ablations/paper_summaries/reward_training_curves_summary.json`.
Raw reward scales are retained;
these curves contain no per-group variance or collapse-rate measurements.

## Pass display projection

At the author's request, pass percentages are displayed on the nearest
integer-count grid for their stated population, using round-half-up for
counts and two decimal places for percentages. This is a projection of
reported rates, not recomputation from observed task verdicts. Each stratum
and the All column are projected separately; the resulting counts are not
guaranteed to be additive. Appendix H explains the transformation.
`results/01_rq1_main_verification/paper_summaries/pass_count_projection.json`
preserves every original percentage
and its projected count/value. `scripts/project_pass_counts.py` reproduces
the transformation; original evaluation reports remain unchanged.
