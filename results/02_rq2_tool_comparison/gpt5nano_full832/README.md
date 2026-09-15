# GPT-5-nano Full-832 final evaluation

This directory contains the target-hidden final comparison of AutoSpec,
Clause2Inv, SESpec, True Naive, LoopGym-R1-NoH, LoopGym-R1-H, LoopGym-R5-H,
LoopGym-R10-H, and Daikon. R4-H is excluded from the final comparison.

## Main outputs

- `final_report.md`: frozen seven-method base report (retained for provenance).
- `summary.csv`: aggregate accuracy, rejection, time, and token metrics.
- `final_results.csv`: frozen seven-method base per-task table.
- `latest.jsonl`: complete latest rows used to build the report.
- `methods/<method>/`: separate final output for each method.
- `failures.csv`: generation, verification, scoring, and support failures.
- `completion_audit.json`: protocol and completeness checks.
- `comparison_table_9methods.md`: current final comparison table.
- `final_results_9methods.csv`: compact 9-method per-task result (7,488 rows).
- `completion_audit_9methods.json`: R5 no-reroll and final-table audit.
- `r5_results.csv`, `r5_summary.json`, `r5_protocol.json`: complete R5-H result.

## Reproduction state

- `protocol.json` and `task_manifest.jsonl`: frozen evaluation protocol.
- `samples/` and `samples_manifest.jsonl`: fixed positive/negative samples.
- `events/`: append-only result streams.
- `artifacts/`: retained native tool outputs.
- `sespec832_rejudge_native_loop/`: corrected SESpec programs and Frama-C logs.

The corrected SESpec result is 414/832 (49.76%). R5-H verifies 622/832
(74.76%) with exactly five rollouts and no reroll; R10-H verifies 638/832
(76.68%) with ten rollouts and no reroll. The current final audit contains 832
rows for each of nine methods and no R4-H row.
