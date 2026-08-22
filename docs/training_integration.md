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
negative example. Build the negative-complete release files with:

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
  "batch_score": 0.83,
  "n_negatives": 118,
  "filter_mode": "cascade(pre-frama->houdini)"
}
```

`base` is the fraction of sampled negative-candidate traces rejected by
the rollout's Houdini survivors. `shapley_credit` allocates the group's
standalone union coverage: a trace covered by `f` rollouts contributes `1/f`
to each. The default reward is
`base + 0.3 * shapley_credit - redundancy_penalty - overflow_penalty`.

Only the first 20 invariant lines enter Houdini. Every later line incurs the
configured overflow penalty. If the sampler produces no negatives, the service
uses a binary Frama-C/WP fallback: reward 1 iff the non-empty canonical
candidate set survives standalone Houdini unchanged, otherwise 0.

The `POST /sample` endpoint exposes sampled positives and negatives for
debugging. `GET /health` reports filter mode and cache size.

For reward ablations, set `reward_variant` to `binary`, `whole_coverage`,
`base`, `base_shapley`, or `full`. `whole_coverage` gives negative coverage
only when every admitted clause in the response survives the positive and
Houdini filters; otherwise it gives zero. For sampler ablations, set
`sampler.negative_sampler` to `random` or `structured`. The two switches are
independent, and omitted switches default to the complete method.
`structured` composes relational perturbations, post-exit continuations,
range/bound escapes, and frame-value perturbations under their fixed family
budgets.

## 3. GRPO recipe

For each RL step:

1. Load `prompt/generate_prompt.txt` and format
   `{program}=visible_program`.
2. Sample a group of responses from the policy.
3. Send `full_program` and the group to `POST /reward`; the service enforces
   target hiding internally.
4. Use `rollout_rewards` as the group rewards, normalize them inside
   the group, and update the policy.

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
