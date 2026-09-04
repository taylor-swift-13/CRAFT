# GPT-5-nano full-832 evaluation

Completion audit: **PASS**.

| Method | Generated | Gen OK | Failed | Timeout | Unsupported | Verified / 832 | Verified / supported | Negative micro | Negative macro | Mean gen. time | Mean API tokens | Mean est. tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| autospec | 832 | 786 | 46 | 0 | 0 | 514 (61.78%) | 514/832 (61.78%) | — | — | 54.77 s (832 rows) | 25154.54 tok (832 rows) | — |
| clause2inv | 832 | 365 | 1 | 0 | 466 | 64 (7.69%) | 64/366 (17.49%) | 6.27% | 8.52% | 29.04 s (366 rows) | — | 385.80 tok (366 rows) |
| sespec | 832 | 830 | 2 | 0 | 0 | 414 (49.76%) | 414/832 (49.76%) | 59.07% | 59.63% | 117.31 s (832 rows) | 44807.05 tok (830 rows) | — |
| naive | 832 | 832 | 0 | 0 | 0 | 379 (45.55%) | 379/832 (45.55%) | 56.05% | 56.68% | 28.87 s (832 rows) | 4493.37 tok (832 rows) | — |
| loopgym_r1_no_houdini | 832 | 832 | 0 | 0 | 0 | 452 (54.33%) | 452/832 (54.33%) | 66.84% | 66.81% | 53.52 s (466 rows) | 5849.64 tok (466 rows) | 630.14 tok (366 rows) |
| loopgym_r1_houdini | 832 | 832 | 0 | 0 | 0 | 504 (60.58%) | 504/832 (60.58%) | 71.25% | 69.58% | 66.60 s (832 rows) | 6928.30 tok (832 rows) | — |
| loopgym_r4_houdini | 832 | 832 | 0 | 0 | 0 | 646 (77.64%) | 646/832 (77.64%) | 82.04% | 81.30% | 293.72 s (466 rows) | 29789.57 tok (466 rows) | 3114.01 tok (366 rows) |

Timing note: mean generation time is the arithmetic mean of recorded per-task durations over the displayed row count. Reused rows without per-task timing are not imputed. Reconstructed batch wall time remains in `efficiency_batches.csv`.

Token note: averages use the displayed exact or estimated row count. API-reported and estimated usage are never combined; unknown usage is not imputed.

AutoSpec is sourced from the strict rerun in
`results/autospec_strict_usage_v2/autospec_strict_statistics.json`; its
negative-rejection columns are blank because that diagnostic was not rerun.

Artifacts:

- `final_results.csv`: one compact row per method/task.
- `failures.csv`: generation, scoring, verification, and unsupported details.
- `completion_audit.json`: machine-readable completeness checks.
- `latest.jsonl`: full latest result objects, including raw artifact paths.
- `summary.csv`: suite-level aggregate metrics and accounting metadata.
- `efficiency_reaudit.csv`: corrected token and wall-time accounting.
- `efficiency_batches.csv`: reconstructed batch/session timing evidence.
