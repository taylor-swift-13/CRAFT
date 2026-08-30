# CRAFT experiment and evidence plan

This file tracks the empirical claims in the paper. A number may enter the
paper only when a program-level artifact records the target-hidden input,
rollouts, final Frama-C/WP judgment, protocol hash, and failure status.
**TBD is not evidence.**

## Research questions

| RQ | Question | Primary evidence |
|---|---|---|
| RQ1 | How do pass@\(k\) and compose@\(k\) scale? | One saved 100-response pool per task for Qwen3-4B, 8B, 14B, 30B-A3B, and Llama 3.1 8B; report \(k\in\{1,10,30,50,100\}\). |
| RQ2 | How much does compose@1 recover from one response across model families? | Paired pass@1/compose@1 judgments of the same response on all 832 tasks. |
| RQ3 | How does CRAFT compare with specialized tools? | Target-hidden adaptations, fixed native budgets, common 832-task denominator, and common restored-target judge. |
| RQ4 | Does RL improve the deployed composed inference curve? | Matched before/after-RL checkpoints for Zero and SFT initializations. |
| RQ5 | Which reward and sampler mechanisms matter? | Three training seeds per reward or sampler variant, with all other settings fixed. |

## Common information boundary and judge

The model sees function preconditions, the executable prefix, loop guard, and
body, but not target set \(Q\) or non-contract comments. Target-derived verifier failures are never fed back before
the invariant set is fixed. The original source is then restored and
Frama-C/WP checks establishment, preservation, and \(Q\)-sufficiency under the
same 5-second per-obligation budget. Parse errors, timeouts, unsupported
attempted inputs, and missing rows are failures.

CRAFT evaluation runs share masking, prompting, response caps, splitting,
normalization, and Houdini. External tools retain their fixed native
orchestration; their budgets are fixed but not claimed to be call- or
token-matched. LLM-based RQ3 rows use gpt-5-nano.

## Metrics

- pass@k: for a pool of \(n=100\) responses with \(c\) successes, compute
  \(1-\binom{n-c}{k}/\binom{n}{k}\) per program and average over programs.
- compose@k: union the first \(k\) saved responses, apply Houdini, restore
  \(Q\), and report the verified fraction. It is a fixed-prefix measurement,
  not an all-subset unbiased estimate.
- \(k_{95}\): the smallest measured \(k\) whose compose@\(k\) reaches 95% of
  that pool's compose@100.
- Efficiency: mean total tokens and end-to-end time per attempted task.
  Provider usage is used for APIs; serving-tokenizer counts are used locally.

RQ2 compares aggregate pass@1 and compose@1 on the same saved response, so the
rate difference introduces no additional model calls.

## RQ5 variants

Reward variants map directly to the released service:

| Paper name | Service value | Difference |
|---|---|---|
| Binary Inductiveness | binary | all-or-nothing inductiveness |
| Whole-Rollout Strength | whole_coverage | dense coverage only if the whole response survives |
| Clause-Decomposed Strength | base | coverage of pooled-filtered clauses assigned back by provenance |
| Full Compositional Credit | full | add cross-rollout Shapley allocation |

The sampler contrast is random versus structured, with identical execution,
cleaning, seed, family-independent trace cap, and reward. Primary
effectiveness is compose@10; also report pass@1, compose@1, compose@100,
\(k_{95}\), pre-normalization within-group reward variance, and expanded-sweep
reachability collision.

## Evidence status

| Evidence block | Current status |
|---|---|
| RQ1 six-checkpoint curves | Values present, per stratum and whole set (`paper/artifacts/v4/probe_stratum_grid.json`; the same file archives the per-stratum grids of the five Zero-initialized RL runs at step 200); raw pool location must be recorded in the final artifact manifest. |
| RQ2 GPT-5-nano/mini/default and DeepSeek paired results | Program-level artifacts present. |
| RQ2 Claude paired accuracy | Aggregate values are in the manuscript, but the 832-task program-level artifact location is missing; only the disclosed 20-task cost sample is present. |
| RQ2 local Qwen/Llama rows | Bare (pass) and +pipeline (compose) rows present for all six base checkpoints, per stratum, from `paper/artifacts/v4/probe_stratum_grid.json` (rendered by `paper/scripts/render_probe_stratum.py`, which also fills `tab:probe-stratum`).  SFT, SFT+RL, and no-pipeline trained rows missing. |
| RQ3 common tool comparison | Program-level CRAFT/tool artifacts present; Clause2Inv token count is estimated and limited to supported tasks. |
| RQ4 Zero to RL-Zero | The finalized pre-RL aggregate now uses the same Qwen3-8B base curve as RQ1. The canonical program-level pool artifact and training manifest must still be archived before submission. |
| RQ4 SFT to SFT+RL | Missing. |
| RQ5 reward ablation | Re-measured full-grid values present for Binary / Whole-Rollout / Clause-Decomposed / Full from the Zero initialization (tab:reward-ablation, fig:reward-ablation); canonical program-level pool artifact still to be archived. |
| RQ5 sampler ablation | Re-measured matched Structured vs Random runs under the full reward present (tab:sampler-ablation); canonical program-level pool artifact still to be archived. |
| Training configuration | Optimizer, learning rate, batch/group sizes, update count, checkpoint IDs, seeds, and hardware manifest missing. |
| Training--test overlap | Reproducible via paper/scripts/audit_train_test_overlap.py; input hashes are in paper/artifacts/train_test_overlap.json. |
| Prompt provenance sanitation | **Submission rerun required.** The old model-facing transform preserved non-contract comments in 478 evaluation files: all 466 Loopy sources contain a `// Source:` path (98 encode an outcome label such as `true-unreach-call`, `safe`, or `ok`), and 12 NLA files contain generic section comments. The shared masking code now removes ordinary comments while retaining ACSL contracts. Every affected generation result predating this fix must be regenerated or supported by a predeclared sensitivity study. |
| Training-prompt sanitation | **Clean artifacts generated; retraining required.** `paper/scripts/sanitize_training_prompts.py` rebuilds one canonical prompt revision, checks every unique RL source with the Frama-C kernel, and filters SFT clauses with the deployed 5-second-per-obligation Houdini judge. The final artifacts contain 37,481 RL and 3,200 SFT rows; sanitation, per-program power rewrites, and hashes are recorded under `paper/artifacts/`. Retrain from these outputs before reporting RQ4/RQ5. |

An artifact sweep finds non-contract comments in 6,971 of 11,717 saved
`results/**/input.hidden.c` files, including complete GPT-5, GPT-5-mini,
DeepSeek, AutoSpec, and gpt-5-nano result directories. These file counts include
retries and pilots and are not task denominators; they establish that the issue
is present in saved model-facing inputs rather than only in raw benchmark files.

## Required artifact schema

Store one JSONL row per (RQ, system, checkpoint, seed, program, k):

~~~json
{
  "rq": "RQ4",
  "suite": "linear",
  "program_id": "10",
  "source_sha256": "...",
  "hidden_source_sha256": "...",
  "protocol_sha256": "...",
  "system": "craft",
  "checkpoint": "...",
  "seed": 0,
  "target_hidden": true,
  "k": 10,
  "rollouts": [],
  "final_invariants": [],
  "verified": false,
  "failure_status": "target_not_proved",
  "prompt_tokens": 0,
  "completion_tokens": 0,
  "wall_seconds": 0.0
}
~~~

Every aggregate table must be regenerated from these rows, retain the common
denominator, and record the exact script and input hashes used to produce it.

## RQ1--RQ4 consistency gate

RQ1 and the pre-RL side of RQ4 now share one finalized Qwen3-8B base curve.
Before submission, archive the corresponding 100-response pool, checkpoint
revision, prompt hash, serving settings, extraction/Houdini configuration, and
program-level judgments so the shared aggregate is reproducible rather than
only synchronized in the manuscript and plotting code.
