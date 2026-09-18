# RQ4: design ablations

This directory contains reward, negative-sampler, coverage, clause-cap,
filter-order, and target-visibility experiments.

- `negative_sampler_*/` and `paired_sampler_coverage_auc/` retain sampler
  runs and paired diagnostics.
- `gold_invariants_832/` contains the verified reference cohort.
- Top-level JSON/Markdown files are sampler variants and audits.
- `paper_summaries/` contains reward curves, Shapley diagnostics, coverage
  summaries, target visibility, and paper-facing ablation tables.
- `paper_summaries/negative_coverage/{legacy,v4}/` keeps the two distinct
  negative-coverage ledgers without filename collisions.

