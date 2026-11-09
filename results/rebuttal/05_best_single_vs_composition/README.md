# Best-of-N versus Compose-of-N: offline experiment

Population: the 466 Loopy programs. Model outputs: the existing GPT-5-nano/minimal 16-response pool, in its recorded order. Budgets: N = 1, 4, 8, 16. No training or new model calls.

## Compared procedures

- **BestCoverage(N):** independently apply the same PreFrama/Houdini filter to each of the first N responses; choose the response with the highest negative-trace coverage. Ties choose the earliest response. Selection never sees the target.
- **OracleBest(N):** report whether any independently filtered response among the first N proves the original target. This evaluation-only upper bound is not a target-hidden selection algorithm.
- **Compose(N):** union the admitted clauses from those exact first N responses and jointly apply PreFrama/Houdini, then verify the original target. Existing results are reused only after checking input hashes, ordered candidate unions, verification-source hashes, and proof settings.

Each response admits at most 20 clauses. Frama-C/WP uses Alt-Ergo and Z3, a 5-second per-goal timeout, and WP parallelism 2 for both filtering strategies.

## Coverage

The existing `ExampleSampler` runs once per program on the target-hidden source, with 12 runs, seed 0, and the structured negative sampler. All candidate responses and compositions share its frozen negative trace groups. Existing `RewardCalculator._rejected_set` and `_to_groups` compute the fraction of trace groups excluded by at least one verified clause. Unknown predicate evaluations do not reject a trace.

When no negative groups are produced, coverage is undefined and BestCoverage uses the predetermined earliest-response fallback. Results must disclose the number of such programs and distinguish full-population verification from coverage statistics on scorable programs. Sampling or proof execution errors remain errors, not verification failures.

## Interpretation

A Compose win over OracleBest establishes that the successful composition cannot be replaced by any single independently filtered response in that same response pool under the tested verifier. A win only over BestCoverage may also reflect imperfect coverage ranking or tie handling. Coverage and the 0.5 label-acceptance threshold provide complementary label-quality diagnostics.

This experiment tests inference and label-construction mechanisms with matched generated-response budgets. It does not measure the downstream outcome of retraining on either label construction method, and does not establish equal verifier time or equal offline construction cost.

Original experiments are read-only inputs. New proof logs, samples, selection records, and reports are written in a separate run directory. Reused verification time must not be reported as zero-cost deployed inference.

## Completed results

The run finished all 466 programs with zero execution errors. Final results and audit details are in `comparison_report.md`; machine-readable counts are in `summary.csv` and `comparison_analysis.json`. All 466 programs produced negative trace groups. Accounting and new-positive-WP-log audits passed, and all frozen original inputs and sources are unchanged.
