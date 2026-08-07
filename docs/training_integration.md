# Training Integration Guide — 训练侧开箱即用

The RL trainer (verl / OpenRLHF / custom GRPO) calls the reward HTTP service
for sampling-backed generation rewards. It does not import `rl_pipeline` or
need a local Frama-C installation.

Keep two program forms in the trainer or dataset:

- `visible_program`: the closed-book function used in the generation prompt,
  with `assert`/`ensures` hidden;
- `full_program`: the original function sent to the reward service,
  including its contract and assertion.

The trainer loads `prompt/generate_prompt.txt`, formats its
`{program}` field with `visible_program`, and uses
`prompt/system_prompt.txt` as the system message.

## 1. Start the service

Docker:

```bash
docker build -f deploy/Dockerfile.reward -t loopgym-reward .
docker run -p 8000:8000 loopgym-reward
curl -s localhost:8000/health
```

The production `filter_mode` should be
`cascade(positive->houdini)`. A plain `positive` mode means Frama-C is
not available and proof filtering is disabled.

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
  "sampler": {"n_runs": 12, "seed": 0}
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
  "reward_mode": "negative_coverage",
  "batch_score": 0.83,
  "should_reroll": false,
  "n_negatives": 118,
  "filter_mode": "cascade(positive->houdini)"
}
```

`base` is the fraction of sampled negative-candidate traces rejected by
the rollout's Houdini survivors. `shapley_credit` allocates the group's
standalone union coverage: a trace covered by `f` rollouts contributes `1/f`
to each. The default reward is
`base + 0.3 * shapley_credit - redundancy_penalty - overflow_penalty`.
The response also includes `hard_bonus` as a deprecated alias for
`shapley_credit`, and requests using the old `w_hard` field remain accepted.

Only the first 20 invariant lines enter Houdini. Every later line incurs the
configured overflow penalty. If the sampler produces no negatives, the service
uses a binary Frama-C/WP fallback: reward 1 iff the non-empty canonical
candidate set survives standalone Houdini unchanged, otherwise 0.

The `POST /sample` endpoint exposes sampled positives and negatives for
debugging. `GET /health` reports filter mode and cache size.

## 3. GRPO recipe

For each RL step:

1. Load `prompt/generate_prompt.txt` and format
   `{program}=visible_program`.
2. Sample a group of responses from the policy.
3. Send `full_program` and the group to `POST /reward`.
4. Use `rollout_rewards` as the group rewards, normalize them inside
   the group, and update the policy.

Keep `sampler.seed` and `sampler.n_runs` fixed within a sweep so
the cached example sets and rewards remain comparable.

## 4. Inference

```python
from rl_pipeline.inference import InferenceFramework

result = InferenceFramework(source).run()
print(result.final_invariants, result.verified)
```

Each attempt generates rollouts, unions their candidates, applies the full
Houdini fixpoint, and performs final Frama-C verification. Generation and
Houdini see only the target-free program; final verification uses the original
target-bearing source.

## 5. Offline scoring

`rl_pipeline/reward/io.py` reads JSONL or Parquet rollout batches and
writes one reward row per rollout:

```bash
python3 -m rl_pipeline.reward.score_file \
  --input rollouts.jsonl \
  --output rewards.jsonl \
  --runs 12 \
  --seed 0
```

Parquet additionally requires `pandas` and `pyarrow`.

## 6. Gotchas

- Do not expose `assert` or `ensures` in the model prompt.
- A `positive`-only health mode is a degraded fallback, not the
  production reward configuration.
- Keep the generation and system prompts file-backed; do not maintain divergent
  inline copies in the trainer.
- Preserve rollout order when mapping `rollout_rewards` back to the
  sampled responses.
