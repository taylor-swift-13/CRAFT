# LoopGym end-to-end experiment plan

This document is the execution plan behind the paper's empirical section. It
separates **measured reward checks**, **target-visibility diagnostics**, and
**TBD target-hidden runs**. Numbers marked TBD must not be reported as results
until the corresponding raw JSONL and Frama-C logs exist.

## 1. Claims and research questions

| RQ | Claim to test | Primary evidence |
|---|---|---|
| RQ1 | Small models explore too few proof-relevant strategies, while combine+Houdini can exploit only clauses the model actually emits. | Direct pass@\(k\) and combine@\(k\) curves for four official Qwen3 checkpoints. |
| RQ2 | The rollout--combine--Houdini inference framework improves target-hidden verification at a measurable token/time cost. | Fixed-model comparison with external tools and controlled one/five/ten-rollout inference variants. |
| RQ3 | Framework-aligned RL should improve the grouped inference curve rather than optimize pass@1 alone. | Qwen3-8B versus 8B-RL-Zero direct and combine curves. |

The primary endpoint is **target-hidden verification accuracy**, not the number
of clauses accepted by Houdini:

\[
\mathrm{Acc}_{\mathrm{hidden}}(M)=\frac{1}{|\mathcal D|}
\sum_{(P,Q)\in\mathcal D}
\mathbf 1[\mathrm{WP}(P,I_M(P\setminus Q),Q)=\mathrm{valid}].
\]

Here the method fixes \(I_M\) without seeing \(Q\); \(Q\) is restored exactly
once for final evaluation. Parse errors, timeouts, and unsupported attempted
inputs are failures, so every method uses the same denominator.

## 2. Corpora and leakage control

### Linear and nonlinear

- 316 linear programs in `src/input/linear`.
  - 133 from Code2Inv.
  - 84 from SyGuS 2019.
  - 99 from SV-COMP 2024.
- 50 nonlinear programs in `src/input/NLA_lipus`.
  - 30 from LIPuS.
  - 20 from SV-COMP 2024.
- This is the primary test suite because it matches the benchmark boundary used
  by Clause2Inv.

### Loopy

- 466 normalized integer programs in `src/input/Loopy`.
- Use as a transfer/comparison suite, not as 466 additional independent tasks.
- The three official floating-point cases are out of scope.

These collections reuse benchmarks from prior work. A random file split is
therefore insufficient. Before training:

1. recover the upstream source family for every program;
2. normalize the target-hidden program to an AST/skeleton signature;
3. cluster exact matches, alpha-renamings, and control-flow clones;
4. assign whole clusters to train/validation/test;
5. publish the cluster manifest and hashes.

The standard 366-program linear and nonlinear table should remain an evaluation
table. If these examples are used for RL training, report a separate held-out
split and never compare that trained-on subset with published zero-shot
baseline counts.

## 3. Target-hidden baseline protocol

Published Loopy, Clause2Inv, and symbolic-solver scores are excluded from the
main table. They solve a target-visible problem and therefore answer a
different question. A baseline is eligible only if all of the following hold:

1. generation receives \(P\setminus Q\), never the assertion or postcondition;
2. ranking, combination, counterexample construction, and repair receive no
   target text, target-derived constraint, or safety verdict;
3. the invariant set is fixed before \(Q\) is restored;
4. correctness is measured by the same final Frama-C/WP command and timeout;
5. every attempted benchmark input remains in the denominator.

The controlled adaptations are:

- **Loopy-no-\(Q\):** mask \(Q\) in both generation and repair prompts. During
  search, expose only invariant initiation and preservation failures. Do not
  expose a final-safety failure until the candidate is fixed and scored.
- **Clause2Inv-no-\(Q\):** run the clause generator on the masked program.
  Permit only initiation and consecution constraints when filtering or
  combining clauses; remove safety counterexamples and every constraint
  derived from \(Q\).
- **iRank-no-\(Q\):** generate the same fixed target-hidden candidate pool used
  by the unranked control. Remove \(Q\), assertion text, and target-derived
  verifier outcomes from ranker inputs and features.
- **Direct-no-\(Q\):** generate once from the masked program, then restore
  \(Q\) only for final scoring.
- **LoopGym:** use target-free rollout and Houdini throughout;
  restore \(Q\) only for the final WP check.

Target-visible executions may be reported in a separate diagnostic table to
measure shortcut size, but never as competing baselines or as evidence of
target-hidden accuracy.

## 4. Controlled systems

All neural rows must use the same base checkpoint, prompt token budget,
temperature, and total number of sampled responses.

| System | Target visible during search? | Candidate processing | Purpose |
|---|---:|---|---|
| Direct-1-no-\(Q\) | no | one rollout, then restored-\(Q\) verification | Single-sample target-hidden baseline. |
| Loopy-no-\(Q\) | no | independent generation and target-free repair | Adapts Loopy to the same information boundary. |
| Clause2Inv-no-\(Q\) | no | target-free clause generation and combination | Adapts Clause2Inv to the same information boundary. |
| iRank-no-\(Q\) | no | rank the same \(G\) target-hidden rollouts, then score with restored \(Q\) | Tests ordering without target leakage. |
| Union+Houdini | no | merge \(G\) rollouts and run \(\mathsf H_P\) | Isolates complementary clauses and pruning. |
| Full LoopGym | no | target-free generation, union, and Houdini | Main inference system. |

Recommended primary budget:

- \(G=8\) initial rollouts, because Loopy reports saturation beginning around
  eight completions and the repository CLI already defaults to eight.
- Three generation seeds per checkpoint; three independent RL training seeds
  for the primary trained-system comparison.
- Per-program limits: 600 s total, 30 s per WP goal, fixed maximum output
  tokens, and no re-roll in the headline table. Report re-roll separately.

## 5. Metrics and failure taxonomy

### Effectiveness

- `Verified@budget`: original target restored and every Frama-C/WP goal proved.
- `Inductive`: at least one nontrivial clause survives Houdini.
- `Exit gap`: inductive survivors exist, but the restored target fails.
- `Copy rate`: a generated clause is an exact normalized copy of the target.
- `pass@k`: only for independent sampling rows; do not use it for pooled
  systems where candidates interact.

Report Wilson 95% confidence intervals for per-program proportions and paired
bootstrap intervals for differences on the same program set.

### Efficiency

- model calls and generated tokens;
- syntax/precheck/full-Houdini/final-WP invocations;
- wall time and peak memory;
- verified programs per 100 full verifier calls;
- average values over solved tasks and over all attempted tasks, clearly
  distinguished.

### Failure buckets

1. no parseable invariant;
2. syntax/scope failure;
3. initiation failure;
4. preservation failure;
5. inductive but insufficient for \(Q\);
6. timeout/tool failure.

The fifth bucket is the key evidence for the paper's distinction between loop
invariant generation and target-hidden loop verification.

## 6. Training ablations

Use the same training prompts and data order.

| Variant | \(w_b\) | \(w_s\) | \(w_r\) | \(w_o\) | Expected diagnostic |
|---|---:|---:|---:|---:|---|
| Base checkpoint | -- | -- | -- | -- | Pre-training reference |
| Inductiveness-only RL | binary | 0 | 0 | 0 | Shows why soundness alone is weak |
| Base-only RL | 1.0 | 0 | 0 | 0 | Standalone discrimination only |
| No-Shapley RL | 1.0 | 0 | 0.02 | 0.05 | Removes group coverage allocation |
| No-redundancy-cost RL | 1.0 | 0.3 | 0 | 0.05 | Tests conservative semantic-duplicate control |
| No-overflow-cost RL | 1.0 | 0.3 | 0.02 | 0 | Keeps the 20-clause cap but removes its excess-length gradient |
| Full LoopGym | 1.0 | 0.3 | 0.02 | 0.05 | Complete generation objective |
| No negative family | 1.0 | 0.3 | 0.02 | 0.05 | Repeat for relation, over-run, and escape |

Primary training plots:

- verified rate versus training step;
- mean target-free reward and hidden-target verified rate on validation;
- reward variance / all-zero GRPO groups.

## 7. Result-table templates

### Main target-hidden result (all entries currently TBD)

| System | Train signal | Linear / 316 | NLA / 50 | All / 366 | Exit gap | Calls | WP calls |
|---|---|---:|---:|---:|---:|---:|---:|
| Direct-1-no-\(Q\) | none | TBD | TBD | TBD | TBD | 1.0 | TBD |
| Loopy-no-\(Q\) | none | TBD | TBD | TBD | TBD | fixed budget | TBD |
| Clause2Inv-no-\(Q\) | original training | TBD | TBD | TBD | TBD | fixed budget | TBD |
| iRank-no-\(Q\) | original ranker | TBD | TBD | TBD | TBD | 8.0 | TBD |
| Base Union+Houdini | none | TBD | TBD | TBD | TBD | 8.0 | TBD |
| Full LoopGym | base+Shapley-costs | TBD | TBD | TBD | TBD | 8.0 | TBD |

### Target visibility diagnostic

| Model and budget | Visible verified | Hidden verified | Copy rate visible | Copy rate hidden |
|---|---:|---:|---:|---:|
| Base, Direct-1 | TBD | TBD | TBD | TBD |
| Base, Best-of-8 | TBD | TBD | TBD | TBD |
| Full LoopGym, \(m=2\) | -- | TBD | -- | TBD |

### Current measured reward checks

| Check | Coverage | Measured result |
|---|---:|---:|
| Discrimination specifications | 10 | 0 violations; minimum gold base 0.996 |
| Negative-label audit | 366 programs, 486,302 candidate states | 0 hard pair-level collisions |
| Soft audit diagnostic | 366 programs | 269 variable-only overlaps in 18 programs |

## 8. Required raw artifact

Write one JSONL row per `(system, checkpoint, seed, program)`:

```json
{
  "suite": "core366",
  "program_id": "linear/10",
  "source_sha256": "...",
  "system": "loopgym",
  "checkpoint": "...",
  "seed": 0,
  "target_visible": false,
  "n_rollouts": 8,
  "rollouts": [],
  "final_invariants": [],
  "verified": false,
  "failure_bucket": "exit_gap",
  "llm_calls": 8,
  "input_tokens": 0,
  "output_tokens": 0,
  "precheck_calls": 0,
  "houdini_calls": 0,
  "final_wp_calls": 0,
  "wall_seconds": 0.0
}
```

Every number in the paper should be regenerated from this artifact. Do not
populate the main comparison from published target-visible baseline tables.
