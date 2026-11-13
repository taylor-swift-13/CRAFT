### Q1 — Can you clarify the candidate-generation, selection and verification budgets for SFT (single) and composed-label SFT? Under a common candidate pool and matched budgets, do composed labels still outperform selecting a single independently filtered response?

Thank you for suggesting this comparison. We first clarify the original SFT data construction, then present a supplementary controlled comparison.

Both SFT datasets use the same input-program pool, Qwen3-8B generator, and training settings, with approximately equal sizes. Over 93% of the distinct prompts in the composed-label dataset also occur in the single-response dataset.

In the original SFT ablation, construction budgets differ: SFT (single) filters one response and skips examples with no valid clauses. Composed-label SFT jointly filters 16 responses per round for at most five rounds, accepting labels at negative coverage >= 0.5; SFT (single) has no such threshold. The 63.94% versus 23.20% compose@1 result compares these complete construction procedures.

In the supplementary experiment, we compare highest-coverage selection and composition on all 466 Loopy programs using the same first N GPT-5-nano/minimal responses, parser, and Houdini settings. This matches model-call counts and generation token costs.

With this fixed candidate pool, selecting the best single response requires N independent filtering runs, whereas composition filters the clause union jointly. Their filtering workloads differ, with CPU-cost ratios depending on N and the proof tasks. Highest-coverage selection chooses an independently filtered response using shared, frozen negative traces. Targets are hidden during selection and restored for evaluation. "Any single succeeds" counts a program if any independently filtered response proves its target.

| Responses N | Highest-coverage single | Any single succeeds | Composition |
|---|---:|---:|---:|
| 1 | 47.85% | 47.85% | 47.85% |
| 4 | 56.87% | 61.16% | 63.09% |
| 8 | 60.30% | 65.88% | 69.10% |
| 16 | 64.16% | 71.46% | 74.89% |

At N=16, composition verifies 50 more programs than highest-coverage selection (+10.73 percentage points), including 16 programs that no individual filtered response verified under the tested configuration (+3.43 points over "Any single succeeds"). At N=4/8/16, composition also retains every success achieved by an individual response.

Recorded cumulative filtering times are:

| Responses N | Independent filtering (hours) | Joint filtering (hours) |
|---|---:|---:|
| 1 | 2.83 | 2.83 |
| 4 | 13.11 | 8.48 |
| 8 | 26.37 | 13.55 |
| 16 | 52.76 | 22.55 |

At N=16, joint filtering reduces recorded cumulative time by 57.3% while producing higher-quality candidates. Appendix K.6 (Table 21) provides further evidence of lower filtering cost.

Our SFT dataset contains 31,865 distinct inputs. A rough extrapolation of these per-input timings gives about 3,608 versus 1,542 cumulative hours for one N=16 construction round. Together with generation, additional rounds, paired SFT retraining, and evaluation, this exceeded our available time and compute during the two-week rebuttal period. We apologize that we could not complete the paired training comparison; the same-pool results directly support the value of composition for label construction.
