# ICLR 2027 rebuttal experiments (November 2026)

This directory keeps rebuttal additions separate from the original chapter-organized
`results/01_rq1_*` through `results/05_appendix_*` experiments. It preserves completed
results, protocols, per-program data, costs, scripts, and evidence; it does not replace
the original paper tables or experimental records.

| Directory | Experiment | Rebuttal use |
|---|---|---|
| [01_craft16_generation](01_craft16_generation/) | Fresh GPT-5-nano/minimal, 832 programs, 16 responses | Shared Loopy comparison |
| [02_craft16_same_pool_prefixes](02_craft16_same_pool_prefixes/) | Same-pool compose@1/4/8/16, token accounting | Shared Loopy comparison and sampling trend |
| [03_loopy_extraction_fix](03_loopy_extraction_fix/) | Corrected extraction, cached-response re-verification | R1/R2/R3 Q2 |
| [04_composition_vs_iterative_repair](04_composition_vs_iterative_repair/) | Hidden/visible targets, matched 1/4/8 calls, 466 programs | R2 Q1 |
| [05_best_single_vs_composition](05_best_single_vs_composition/) | Matched candidate pool, highest-coverage single versus composition | R1/R3 Q1 |
| [06_invariant_semantic_audit](06_invariant_semantic_audit/) | Strength, reachability exactness, SMT rechecks | R1/R2 Q4 |
| [07_reasoning_budget_comparison](07_reasoning_budget_comparison/) | Medium-prefix cost audit and similar-token minimal/medium comparison | R2 Q3 |
| [08_target_visibility_matched](08_target_visibility_matched/) | Matched target-visible/hidden generation and prefix semantic audits | R1/R2 reuse and visibility analysis |
| [09_loopy_cumulative_success](09_loopy_cumulative_success/) | Cumulative success at every iteration (running snapshot) | Rebuttal supporting evidence |
| [10_additional_artifacts_and_coverage](10_additional_artifacts_and_coverage/) | Additional pilots, probes, diagnostics and complete artifact coverage | Rebuttal supporting evidence |
| [11_reuse_examples_readonly](11_reuse_examples_readonly/) | 30 existing certified programs; manually derived reuse goals | Read-only illustrative reuse analysis |
| [12_reuse_exit_goal_comparison](12_reuse_exit_goal_comparison/) | Paired offline checks of 240 post-loop goals on 30 frozen summaries per arm | Illustrative multi-goal reuse comparison |
| [13_common_success_exit_reuse](13_common_success_exit_reuse/) | All 578 jointly verified programs; symmetric source-template exit-goal probe with full coverage accounting | Multi-goal reuse analysis |

## Current response draft

The latest response is [rebuttal.md](../../rebuttal.md) at the repository root.
[Individual comments](responses/) are exported from that draft and checked against
the 5,000-character per-comment limit. The label-construction comparison now also
includes [recorded filtering times](05_best_single_vs_composition/cost_audit/REPORT.md)
and the explicitly extrapolated training-scale estimate.

## Evidence and reproduction

Every copied source is listed in `provenance.json`. `manifest.json` records SHA-256
hashes for every archived file. Existing compressed bundles are preserved byte for
byte, divided into at most 24 MiB parts. Each `*.index.json` records the complete
archive hash, ordered parts, and member hashes. To restore a bundle:

```sh
python3 results/rebuttal/restore_evidence.py \
  results/rebuttal/01_craft16_generation/evidence/evidence_complete_20261108.tar.gz.index.json \
  --out /tmp/rebuttal_craft16_evidence
```

Scripts retain their original run paths and settings. The completed generation,
verification, and cost records can be inspected without making new API calls.
Restore evidence and adapt historical path constants before rerunning a script.
The original model-generation scripts require the original service configuration;
local proof rechecks require the recorded Frama-C/Why3/SMT environment.

Final runs and their recovery histories are retained. Superseded pilot outputs,
duplicate pre-completion bundles, connectivity probes, temporary verifier scratch
directories, and unrelated untracked repository files are excluded. Original data
and historical reward/seed metadata are not edited. This archive does not create
new evidence for the author-supplied Shapley repeat-training claims.

The semantic audit retains all 832 records for traceability; the rebuttal denominator
is the 602 originally verified programs. Loopy repair results reuse existing replies;
costs describe accessed cached calls. The reasoning-budget comparison uses different
prompt/batch configurations. See each experiment's README/ARCHIVE and protocol files.

The supplemental archive 10 additionally preserves non-final pilots, probes and diagnostics
previously omitted from 01–08. Its source inventory maps every scoped file to an
existing hash-identical archive member or a new supplemental member. These records
are historical evidence, not replacements for final experiment results.
