# Verification timing from the label-construction comparison

Sum of per-task elapsed seconds, not CPU time or parallel wall-clock makespan.

No LLM generation, negative-trace sampling, or coverage scoring. Excluding-goal estimate subtracts judgment.judge_seconds from task.seconds; includes remaining task overhead.

Independent and joint records were collected in separate runs with different concurrency/system load. No matched CPU-cost measurement for both arms. Identical candidate clause sets are deduplicated within each program/prefix. Reused records are charged their original elapsed time.

| N | Independent filtering + evaluation (h) | Joint filtering + evaluation (h) | Ratio | Independent excluding goal check (h) | Joint excluding goal check (h) | Ratio |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 3.568 | 3.568 | 1.00 | 2.834 | 2.834 | 1.00 |
| 4 | 16.406 | 9.346 | 1.76 | 13.107 | 8.476 | 1.55 |
| 8 | 33.077 | 14.525 | 2.28 | 26.368 | 13.553 | 1.95 |
| 16 | 66.177 | 23.501 | 2.82 | 52.762 | 22.550 | 2.34 |

All 1864 composition source hashes match the frozen comparison protocol. Original experimental files were not modified.
