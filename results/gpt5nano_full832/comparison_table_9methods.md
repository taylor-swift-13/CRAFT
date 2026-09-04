# Full-832 final comparison (R5-H replaces R4-H)

AutoSpec uses the strict exact-usage rerun in
`results/autospec_strict_usage_v2/autospec_strict_statistics.json`.

| Tool / configuration | Correct / 832 | Mean generation time | Mean total tokens | Negative micro | Negative macro |
|---|---:|---:|---:|---:|---:|
| AutoSpec | 514 (61.78%) | 54.77 s (832 rows) | 25,154.54 exact (832 rows) | — | — |
| Clause2Inv | 64 (7.69%); 64/366 supported (17.49%) | 29.04 s (366 supported rows) | 385.80 estimated (366 rows) | 6.27% | 8.52% |
| SESpec | 414 (49.76%) | 117.31 s (832 rows) | 44,807.05 exact (830 rows) | 59.07% | 59.63% |
| Naive | 379 (45.55%) | 28.87 s (832 rows) | 4,493.37 exact (832 rows) | 56.05% | 56.68% |
| LoopGym R1-NoH | 452 (54.33%) | 53.52 s (466 rows) | 5,849.64 exact (466); 630.14 estimated (366) | 66.84% | 66.81% |
| LoopGym R1-H | 504 (60.58%) | 66.60 s (832 rows) | 6,928.30 exact (832 rows) | 71.25% | 69.58% |
| LoopGym R5-H (no reroll) | 622 (74.76%) | 284.81 s (832 rows) | 33,404.56 exact (832 rows) | 80.08% | 79.43% |
| LoopGym R10-H (no reroll) | 638 (76.68%) | 536.81 s (832 rows) | 68,953.35 exact (832 rows) | 80.92% | 80.95% |
| Daikon | 174 (20.91%) | 4.89 s (832 rows) | 0 (not called, 832 rows) | 50.73% | 51.14% |

Correctness is direct common Frama-C validation against each restored hidden target.
R5-H and R10-H use exactly five and ten target-hidden rollouts, respectively,
followed by union and Houdini, with reroll disabled. R5-H replays the exact first
five R10-H responses and independently recomputes union, Houdini, and validation;
its reported cost includes the original response latency and exact token usage.
R4-H is intentionally excluded from this final comparison.

Daikon receives no assertion, postcondition, or negative traces and uses no Houdini.
