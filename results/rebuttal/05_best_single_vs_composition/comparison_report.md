# Best-of-N versus Compose-of-N

Status: complete; 466/466 Loopy programs.

Existing GPT-5-nano/minimal responses only; no training and no new model calls.

| N | BestCoverage | OracleBest (upper bound) | Compose | Compose-only vs oracle | Oracle-only vs compose |
|---|---:|---:|---:|---:|---:|
| 1 | 223/466 (47.85%) | 223/466 (47.85%) | 223/466 (47.85%) | 0 | 0 |
| 4 | 265/466 (56.87%) | 285/466 (61.16%) | 294/466 (63.09%) | 9 | 0 |
| 8 | 281/466 (60.30%) | 307/466 (65.88%) | 322/466 (69.10%) | 15 | 0 |
| 16 | 299/466 (64.16%) | 333/466 (71.46%) | 349/466 (74.89%) | 16 | 0 |

BestCoverage selects by common negative-trace coverage after independent filtering, without targets. OracleBest asks whether any independently filtered response proves the target and is not a deployable selector. Compose jointly filters the same prefix union.

Matched budget means the same generated responses, not equal verifier compute. These results test inference and label-construction mechanisms; they do not measure retrained SFT performance.

At N=16, Compose exceeds BestCoverage by 50 programs (10.73 percentage points), and OracleBest by 16 programs (3.43 points). At N=4/8/16, there are no OracleBest successes lost by composition. Coverage ties are common (357/466 programs at N=16), which is why the goal-aware OracleBest upper bound is reported separately.

All 466 programs have nonempty sampled negative trace groups. Mean coverage and the number of programs reaching the paper's 0.5 coverage threshold are:

| N | BestCoverage mean | Compose mean | BestCoverage >= 0.5 | Compose >= 0.5 |
|---|---:|---:|---:|---:|
| 1 | 54.17% | 54.17% | 264 | 264 |
| 4 | 67.00% | 67.65% | 328 | 333 |
| 8 | 70.41% | 71.39% | 342 | 351 |
| 16 | 73.87% | 75.15% | 359 | 371 |

The local experiment completed 6,931 new unique independent-candidate proof jobs and sampled all 466 programs, reusing 466 existing candidate proof records. It used 24 workers after an initial 16-worker phase. The observed interval, including the pilot and resume, was 2h 48m 12s. New model calls, model tokens, and training runs were all zero. This timing is bookkeeping for this cached offline analysis, not a comparison of deployed inference latency.

The independent accounting audit passed for all 466 programs, including reconstructed response prefixes, target-blind coverage choices, oracle results, and N=1 equality. All 3,763 frozen input/source hashes remain unchanged. The 1,374 completed records retained during the concurrency change are also unchanged.

The final WP logs for all 3,398 newly verified unique independent candidates passed a separate audit: 52,231 goals, including 3,979 target goals, were explicitly valid. The total includes 157 properties that Frama-C marks `Valid (Unreachable)` without printing a separate formula block. Reused proofs retain their existing provenance; this log count covers new positive candidate records only.

Detailed evidence: `summary.csv`, `per_program.csv`, `candidate_details.csv`, `paired_witness_cases.csv`, `comparison_audit.json`, `wp_log_audit.json`, `resource_summary.json`, and the native run paths in `protocol.json`. `example_loopy18.md` gives a concrete composition-only case, including a coverage tie.
