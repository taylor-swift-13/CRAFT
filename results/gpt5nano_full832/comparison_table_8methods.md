# Full-832 comparison with Daikon

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
| LoopGym R4-H | 646 (77.64%) | 293.72 s (466 rows) | 29,789.57 exact (466); 3,114.01 estimated (366) | 82.04% | 81.30% |
| Daikon | 174 (20.91%) | 4.89 s (832 rows) | 0 exact (832 rows) | 50.73% | 51.14% |

Correctness is direct common Frama-C validation of each method's final
invariants against the restored hidden target. Daikon 5.8.24 receives no
assertion, postcondition, or negative traces and uses no Houdini. Its time
includes fixed-trace hash/read/export and Daikon execution, but not the
one-time creation of the shared frozen traces.

The seven original rows are copied unchanged from the completed seven-method
report. Their negative columns retain that report's existing scoring pipeline;
Daikon's negative columns are computed directly from its native final clauses,
as requested. Therefore the negative columns are recorded here but should not
be interpreted as a strictly identical post-processing comparison.
