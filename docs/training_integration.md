# Training Integration Guide — 训练侧开箱即用

The RL trainer (verl / OpenRLHF / custom GRPO) calls the reward HTTP service
for sampling-backed generation rewards. It does not import `rl_pipeline` or
need a local Frama-C installation.

Keep two program forms in the trainer or dataset:

- `visible_program`: the closed-book function used in the generation prompt,
  with `assert`/`ensures` and non-contract comments hidden;
- `full_program`: the original function retained for the final judge. It may be
  sent to the reward endpoint for convenience; the service strips
  `assert`/`ensures` before both sampling and inductiveness filtering.

The trainer loads `prompt/generate_prompt.txt`, formats its
`{program}` field with `visible_program`, and uses
`prompt/system_prompt.txt` as the system message.

## 0. Build the canonical training datasets

The original `0803` archives predate the current prompt and invariant
interface. They are provenance inputs and are not included in the tracked
release; pass their paths explicitly when regenerating the clean artifacts.
The script never overwrites its inputs. Initialize the Frama-C opam switch,
then run:

```bash
eval "$(opam env --switch=frama-c.27.1 --set-switch)"
CRAFT_WP_PAR=2 conda run -n ASGSE \
  python paper/scripts/sanitize_training_prompts.py \
  --rl-input /path/to/loopgym_rl_0803.parquet \
  --sft-input /path/to/loopgym_sft_0803.json \
  --verify-rl-syntax --rl-syntax-jobs 32 \
  --verify-sft --wp-timeout 5 --jobs 16
```

This first writes the auditable intermediate files
`traindata/craft_rl_clean.parquet` and `traindata/craft_sft_clean.json`. Do not
train from these intermediates: a syntactically valid loop can still have no
sound perturbation axis from which the deployed sampler can construct a
negative trace. Build the negative-complete release files with:

```bash
python paper/scripts/audit_training_negative_coverage.py rl --jobs 16
python paper/scripts/filter_training_by_negative_coverage.py sft
python paper/scripts/filter_training_by_negative_coverage.py rl
```

The trainer inputs are
`traindata/craft_rl_negative_complete.parquet` and
`traindata/craft_sft_negative_complete.json`. The filter fails if its coverage
ledger is incomplete and retains a loop only when the sampler produced at
least one valid negative trace. SFT uses the same source-hash-indexed ledger as
RL; this avoids sampling identical loops twice while still failing closed if
an SFT source is absent or changed. Loops whose persistent state is entirely
tainted by nondeterministic updates are quarantined in the filter report; they
are not assigned fabricated negatives. Re-run the audit and filter whenever
the clean data or sampler changes.

Both intermediate and release files use the current system and generation
prompts and contain only supported target-hidden scalar-integer, single-loop
programs. For archived `power` clauses, fixed exponents are expanded into
explicit multiplication. When two equations contain the same symbolic power,
the cleaner attempts to eliminate it and derive a power-free polynomial
relation; symbolic powers are never approximated by a finite expansion.
Reducible product equalities and guarded copies of an already-unconditional
conclusion are discarded as weak or redundant. SFT
answers additionally remove remaining helper calls, malformed or out-of-scope
clauses, conservative duplicates, obvious tautologies, weaker dominated
constant bounds, and clauses that do not survive the deployed Frama-C/WP
Houdini filter. Legacy integer casts and Unicode comparison operators are
normalized before this gate. For bounds-only answers, the cleaner may propose
one conservation equality only for two variables with single unconditional
constant updates; it is retained only after Houdini/WP validation. The cleaner
also minimizes entry labels: `\at(v,LoopEntry)`
is retained only when `v` is a local whose final pre-loop assignment is directly
`unknown()`/`unknownN()`.  Parameters are rewritten to `Pre`, and deterministic
local initializers are recursively inlined; a clause is rejected if a remaining
entry value cannot be represented under this rule. Each answer is capped at 20
clauses. The 5-second setting is
per WP proof obligation, not per training record. Per-problem power rewrite
decisions are saved in `paper/artifacts/power_rewrite_audit.json`. Train only
from these clean outputs.

## 0b. Curate the pool: test-related, de-duplicated, gradient-bearing

The released `craft_*_final` files are a sanitized pool, not a curriculum:
35,100 unique RL programs collapse to ~2,200 loop shapes (alpha-renamed,
constant-abstracted guard + body), the two largest shapes
(`while (v0 < NUM) { v0 = v0 + 1; }` and `while (v0 < v1) { ... }`) hold 36%
of the pool, ~40% of programs use the `while (1) { if (!(c)) break; ... }`
idiom that never occurs in the 832-program evaluation corpus, and ~30% of
loops have no relation-family negatives at all, so a bounds-only answer
already saturates the coverage reward. GRPO gets no gradient from saturated
or hopeless groups. The curation below fixes all of that target-independently.

```bash
eval "$(opam env --switch=frama-c.27.1 --set-switch)"   # Frama-C for stages 3-4

# 1. canonicalize the break idiom (semantics-preserving; restores a real guard)
python paper/scripts/canonicalize_training_pool.py rl \
  --input traindata/craft_rl_pool.parquet --output traindata/craft_rl_canonical.parquet \
  --report paper/artifacts/v4/rl_canonicalization.json
python paper/scripts/canonicalize_training_pool.py sft \
  --input traindata/craft_sft_archive.json --output traindata/craft_sft_canonical.json \
  --report paper/artifacts/v4/sft_canonicalization.json

# 2. schema-current negative ledger (gcc only; ~1 h with 6 workers)
python paper/scripts/audit_training_negative_coverage.py rl \
  --input traindata/craft_rl_canonical.parquet \
  --ledger paper/artifacts/v4/rl_negative_coverage.jsonl \
  --manifest paper/artifacts/v4/rl_negative_coverage_manifest.json --jobs 6
#   (use --start/--stop slices on small machines; the pool is memory-hungry)

# 3. static gates + near-duplicate removal + test relatedness + shape cap
python paper/scripts/curate_training_pool.py rl \
  --input traindata/craft_rl_canonical.parquet \
  --ledger paper/artifacts/v4/rl_negative_coverage.jsonl \
  --output traindata/craft_rl_train.parquet --report paper/artifacts/v4/rl_curation_report.json
python paper/scripts/curate_training_pool.py sft \
  --input traindata/craft_sft_canonical.json \
  --ledger paper/artifacts/v4/rl_negative_coverage.jsonl \
  --output traindata/craft_sft_curated.json --report paper/artifacts/v4/sft_curation_report.json \
  --per-shape-cap 32

# 4. (cluster, vLLM) policy frontier: keep programs whose G-rollout group has reward variance
python experiments/measure_policy_frontier.py --input traindata/craft_rl_train.parquet \
  --ledger paper/artifacts/v4/rl_frontier.jsonl --policy <sft-checkpoint> --group 8 \
  --apply --output traindata/craft_rl_frontier.parquet --report paper/artifacts/v4/rl_frontier_report.json

# 5. SFT targets distilled from the pipeline (OpenAI-compatible API + local Frama-C)
python paper/scripts/select_sft_programs.py --sft traindata/craft_sft_curated.json \
  --rl traindata/craft_rl_train.parquet --output traindata/craft_sft_programs.json \
  --report paper/artifacts/v4/sft_program_selection.json --target 6000
OPENAI_API_KEY=... OPENAI_BASE_URL=... CRAFT_MODEL=... \
python experiments/synthesize_sft_from_rollouts.py all --dataset sft \
  --input traindata/craft_sft_programs.json --root results/sft_synth \
  --output traindata/craft_sft_train.json --report paper/artifacts/v4/sft_synthesis_report.json --k 8
```

Gates applied by `curate_training_pool.py` (all recorded in the report and
in `extra_info.curation` of every kept row):

| gate | rule | why |
|---|---|---|
| `duplicate_of_eval` | exact / alpha-renamed / constant-abstracted copy of an evaluation program | leakage |
| `unscorable` | sampler failed or no negatives (schema-current ledger) | masked group |
| `too_few_traces` | `< 8` negative traces | reward takes too few values |
| `no_relation_signal` | `< 4` relation traces | bounds saturate the reward |
| `bounds_saturated` | relation share `< 0.10` | a bounds-only answer scores >= 0.9 |
| `unrelated_to_eval` | structural cell and control-flow skeleton absent from the evaluation corpus | not what the benchmark tests |
| `shape_cap` | `> 8` programs per loop shape (RL) | the pool is thousands of copies of a few shapes |

Kept rows carry `extra_info.curation = {weight, cell, relatedness,
related_level, stratum_guess, n_negative_traces, relation, tags}`; `weight`
re-balances the survivors toward the evaluation corpus' distribution over
structural cells (clipped to [0.25, 4]). Loop shapes that match an evaluation
program after renaming, constant abstraction and initializer removal are
*related*, not duplicates, and are kept (capped).

SFT targets from `synthesize_sft_from_rollouts.py` are the union of k rollouts
(plus the archival answer) -> interface gate -> Houdini on the target-hidden
program (exactly the inference filter) -> frame equalities of unmodified
variables dropped (`loop assigns` states them) -> z3 implication pruning
(weaker clauses implied by the rest are dropped, strongest first) -> every
relational clause kept, other clauses kept only while they add negative-trace
coverage -> Houdini re-check. Typical answers shrink from ~10 clauses to 3-6
and always contain the relational law when any rollout produced one.

## 1. Start the service

Docker:

```bash
docker build -f deploy/Dockerfile.reward -t craft-reward .
docker run -p 8000:8000 craft-reward
curl -s localhost:8000/health
```

The production `filter_mode` should be
`cascade(pre-frama->houdini)`. A plain `pre-frama` mode means Frama-C is
not available and proof filtering is disabled. The pre-Frama stage rejects
malformed/out-of-scope clauses, removes conservative semantic duplicates, and
checks sampled positive states before any Frama-C process is launched.

Native:

```bash
python3 -m pip install -r deploy/requirements-reward.txt
why3 config detect
python3 -m rl_pipeline.reward.service --host 0.0.0.0 --port 8000
```

## 2. Reward endpoint

Call `POST /reward` once for every group of rollouts sampled from one
program prompt.

```jsonc
{
  "program": "<full C source>",
  "rollouts": [
    {"invariants": ["x >= y", "y >= 0"]},
    "<raw LLM response>"
  ],
  "w_base": 1.0,
  "w_shapley": 0.3,
  "w_redundancy": 0.02,
  "w_overflow": 0.05,
  "max_invariants": 20,
  "reward_variant": "full",
  "sampler": {
    "n_runs": 12,
    "seed": 0,
    "negative_sampler": "structured"
  }
}
```

The response is order-aligned with the submitted rollouts:

```jsonc
{
  "rollout_rewards": [0.41, 0.17],
  "base": [0.35, 0.14],
  "shapley_credit": [0.20, 0.10],
  "redundant_clauses": [0, 0],
  "redundancy_penalty": [0.0, 0.0],
  "overflow_penalty": [0.0, 0.0],
  "reward_variant": "full",
  "negative_sampler": "structured",
  "reward_mode": "negative_coverage",
  "scorable": true,
  "batch_score": 0.83,
  "n_negatives": 60,
  "filter_mode": "cascade(pre-frama->houdini)"
}
```

`base` is the fraction of sampled negative-candidate traces rejected by
the rollout's Houdini survivors. `shapley_credit` allocates the group's
standalone union coverage: a trace covered by `f` rollouts contributes `1/f`
to each. The default reward is
`base + 0.3 * shapley_credit - redundancy_penalty - overflow_penalty`.

Only the first 20 invariant lines enter Houdini. Every later line incurs the
configured overflow penalty. All generated relation and escape traces
enter `base`, Shapley credit, and `n_negatives`. If the sampler produces no
negative traces, the service returns zero rewards with `scorable=false` and
`reward_mode="unscorable_no_negative_traces"`. The trainer must mask the whole
group. Binary Frama-C/WP inductiveness is available only through the explicit
`reward_variant="binary"` ablation.

The `POST /sample` endpoint exposes sampled positives and negatives for
debugging. `GET /health` reports filter mode and cache size.

For reward ablations, set `reward_variant` to `binary`, `whole_coverage`,
`base`, `base_shapley`, or `full`. `whole_coverage` gives negative coverage
only when every admitted clause in the response survives the positive and
Houdini filters; otherwise it gives zero. For sampler ablations, set
`sampler.negative_sampler` to `random` or `structured`. The two switches are
independent, and omitted switches default to the complete method.
`structured` scores guard-preserving relational perturbations and post-exit
escape continuations under fixed family caps of 48 and 12 trace units. It
removes perturbations parallel to a witnessed transition, conditions envelopes
on `Pre`/`LoopEntry`, and generates neither range nor frame families.

## 3. GRPO recipe

For each RL step:

1. Load `prompt/generate_prompt.txt` and format
   `{program}=visible_program`.
2. Sample a group of responses from the policy.
3. Send `full_program` and the group to `POST /reward`; the service enforces
   target hiding internally.
4. If `scorable=false`, mask the whole group. Otherwise use
   `rollout_rewards`, normalize them inside the group, and update the policy.

Keep `sampler.seed` and `sampler.n_runs` fixed within a sweep so
the cached example sets and rewards remain comparable. Also record
`reward_variant` and `sampler.negative_sampler` for every ablation run.

## 4. Inference

```python
from rl_pipeline.inference import InferenceFramework

result = InferenceFramework(source).run()
print(result.final_invariants, result.verified)
```

Each inference call generates one fixed-budget rollout group, unions its
candidates, applies the full Houdini fixpoint, and performs final Frama-C
verification. Generation and Houdini see only the target-free program; final
verification uses the original target-bearing source. A failed final proof
does not trigger another model call.

## 5. Offline scoring

`rl_pipeline/reward/io.py` reads JSONL or Parquet rollout batches and
writes one reward row per rollout:

```bash
python3 -m rl_pipeline.reward.score_file \
  --input rollouts.jsonl \
  --output rewards.jsonl \
  --runs 12 \
  --seed 0 \
  --reward-variant full \
  --negative-sampler structured
```

Parquet additionally requires `pandas` and `pyarrow`.

## 6. Gotchas

- Do not expose `assert` or `ensures` in the model prompt.
- Do not expose source-provenance or other non-contract comments in the model
  prompt; the shared masking function removes them.
- A `positive`-only health mode is a degraded fallback, not the
  production reward configuration.
- Keep the generation and system prompts file-backed; do not maintain divergent
  inline copies in the trainer.
- Preserve rollout order when mapping `rollout_rewards` back to the
  sampled responses.
- Train only from the `*_negative_complete` release files. The `*_clean`
  intermediates intentionally retain quarantined rows for reproducible audits.
