# CRAFT

*(formerly SAM2INV)*

RL pipeline for training a model to generate **ACSL loop invariants** for C
programs, verified with **Frama-C/WP**. It has three independently usable entry
points that share parsing and state modules; reward and inference also share the
Frama-C adapter:

```
   ┌────────────┐        rollouts (a group of        ┌──────────────────┐
   │  Sampler   │         candidate invariant sets)  │    Inference     │
   │  (traces)  │                                    │ generate→union→  │
   └─────┬──────┘                                    │ Houdini→verify   │
         │ pos + neg traces                          └──────────────────┘
         ▼                                            (testing: vLLM + frama-c)
   ┌──────────────┐   per-rollout reward
   │    Reward    │   = base + Shapley group credit - penalties
   │ HTTP service │   (training: called by the RL trainer, e.g. verl)
   └──────────────┘
```

- **Training** uses `prompt/generate_prompt.txt` and calls the **reward** HTTP
  service to score each rollout group.
- **Testing** deploys a trained model to **vLLM** and runs **inference**.
- Both are packaged as Docker images with **frama-c bundled** (no host install).

---

## 1. Sampler — `rl_pipeline/sampler` (the core; the reward's quality depends on it)

**The sampler sees ONLY the loop — never the assert/postcondition.** It executes
the loop and works with *traces* (the group of loop-head variable valuations one
run passes through):

- **positive** = a reachable loop-head valuation (a state of a real trace);
- **negative candidate** = a synthetic trace intended to depart from observed
  loop behavior. Each is stored as its *witness states* (where the synthetic
  history departs from sampled behavior, grouped in `neg_groups`): a one-shot perturbation is a
  singleton ("real prefix + this state"); an escape continuation is one
  group holding its whole past-the-exit segment.  A rollout **rejects** the
  history iff some invariant is false at any witness — and rewards count in
  trace units, so one long fake continuation is one negative, not twenty-four.

Input generation evaluates the full `requires` contract, including numeric C
macros, initialized file-scope integers, negated clauses, and relations between
parameters.

The training signal favors invariants that are sound on sampled positives and
exclude many audited negative candidates. The design is deliberately minimal:
two complementary candidate families, conservative construction guards, and no scoring-side
patch layers. Finite execution is not a proof of global unreachability; the real
Frama-C/WP Houdini stage proves candidate invariants, while the audit below
checks the sampled labels for observed collisions.

Two negative families:

- **relation** — small perturbations around bases whose next three trace
  coordinates were printed, plus a wider local neighborhood around witnessed
  true-guard → false-guard terminal transitions. Every candidate must preserve
  the guard truth value and remain inside the sampled envelope for the same
  `Pre`/`LoopEntry` context; perturbations parallel to a witnessed transition
  are removed because they are merely another reachable trace position;
- **escape** — the loop body executed past a **genuine** exit: real dynamics
  (preserves every relation, linear *and* nonlinear, e.g. `z==x*y`), out of the
  reachable range;

The independent trace-group caps are 48 relation and 12 escape (60 total
maximum), selected round-robin across structural buckets. Range and frame
perturbation families are not generated.

Conservative construction guards:

- states observed **reachable** are never negatives;
- states that could be a **fresh loop entry** (params free, `requires`
  satisfiable) are never negatives — they are reachable under other inputs;
- `unknown*()` call sites are replaced by fresh sampled parameters only during
  concrete execution. Their values remain in each trace's `Pre`/`LoopEntry`
  context, so oracle-affected relation axes no longer disappear globally;
- untracked persistent state, pointer/array state, and non-oracle function calls
  disable arbitrary relation perturbations, but do not suppress a genuinely
  executed post-exit continuation;
- capped/nonterminating runs can still contribute witnessed relation traces,
  but never fabricate an exit continuation.

Supporting mechanics: loops run to their real exit (printing is throttled, not
the execution); inputs satisfy the full multi-clause `requires` incl.
param-vs-param constraints (or sampling fails explicitly); unsigned values keep
their C signedness; perturbation bases retain a dense prefix, distributed
mid-trace points, and the end of every concrete trace; integer macros are bound
before guard/invariant evaluation; far input placement is seed-hashed; ACSL
predicates use C-style truncating `/` and `%`.

The supported sampling model is one braced `while` loop over scalar C `int`
parameters, locals, and file-scope variables. Multiple loops, `for` loops, and
pointer/array parameters fail explicitly instead of returning partial samples.

`unknown()` guards/values are supported. Each syntactic call site is
determinized to a fresh input during sampling; the original oracle-bearing
source is retained for generation and Houdini verification.

```python
from rl_pipeline.sampler import ExampleSampler
es = ExampleSampler(
    source, n_runs=12, negative_sampler="structured"
).sample()  # loop only; no assert used
es.pos(0)      # reachable loop-head valuations
es.neg(0)      # witness states of synthetic negative candidates
es.groups(0)   # witness-index groups, one per candidate trace unit
es.group_families(0)  # relation / escape label for each trace
```
CLI: `python -m rl_pipeline.sampler.example_sampler <file.c>`

Negative-sampler ablations are selected with `negative_sampler` (or the CLI
flag `--negative-sampler`): `random` uses budget-matched unstructured
perturbations, while `structured` composes relational perturbations and
post-exit escapes. `structured` is the default.

**Discrimination harness** — `python -m rl_pipeline.eval.discrimination` scores
rollout families of known quality (gold / loose / trivial / guard / post /
unsound) on benchmark programs and fails on ranking violations — e.g. a weaker
family outscoring gold, or a true invariant getting filtered (sampler
mislabel). Quality discrimination is the sampler's job; production soundness
is delegated to the real Houdini cascade in the reward image.

**Mislabel audit** — `python -m rl_pipeline.eval.mislabel_audit` sweeps the FULL
benchmark suite (366 programs) and fails if any sampled negative shows up as a
positive in larger 24-run samples at seed 0 or seed 9. Pair-level collisions
are hard failures; variable-only overlaps with a different `Pre` are reported
as soft diagnostics.

## 2. Reward — `rl_pipeline/reward`

Scores a **group** of rollouts. For each rollout `A` (in candidate-trace
units — one fake continuation is ONE negative, not twenty-four):

- all clauses are pooled and filtered once, then each surviving clause is
  assigned back to every rollout that proposed it (including a semantically
  equivalent spelling);
- `base[A]`     = candidates rejected by the pooled survivors assigned to `A`;
- `shapley_credit[A]` allocates the union of the group's attributed negative
  coverage: a trace rejected by `f` rollouts contributes `1/f` to each.
  Credits therefore sum exactly to attributed union coverage;
- `reward[A]`   = `1.0·base[A] + 0.3·shapley_credit[A]
  - 0.1·overflow[A]` by default.
  Repeated clauses collapse in the same clause-set construction used at
  inference (the solver-free key handles comparison direction, commutative
  equality/addition/multiplication, harmless identities, and order-preserving
  Boolean association), so they cannot affect Houdini, coverage, or any
  final target, and they are not penalized.
  Soundness is not a scoring patch: when Frama-C is
  available it comes from `PreFramaFilter → Frama-C/WP fixpoint`. The cheap
  stage removes malformed and duplicate clauses before checking sampled
  reachable states; Frama-C remains the authoritative inductiveness check. Unsound
  clauses are pruned without zeroing the whole response. A unique clause with
  no sampled negative coverage is not charged: it may support another
  survivor or an unseen proof target;
- each model response admits at most 20 `loop invariant` lines. Later lines do
  not enter filtering or scoring, and each later line incurs a 0.1 overflow
  penalty. The response exposes `generated`, `accepted`, `overflow`, and
  `overflow_penalty`;
- `batch_score` = candidate traces rejected by `Houdini(∪)`. If no
  negatives are available, the group falls back to binary inductiveness
  (1 iff the response is nonempty and every admitted clause survives
  Houdini) and reports `scorable=false`,
  `reward_mode="binary_fallback_no_negative_traces"`. Binary inductiveness
  is also available unconditionally as the `reward_variant="binary"` ablation.

```python
from rl_pipeline.reward import RewardCalculator
br = RewardCalculator(reward_variant="full").compute(source, rollouts)
br.to_dict()   # rewards, coverage, Shapley credit, batch_score
```

The default `credit_filter_order="pooled"` matches inference and requires one
nonempty Houdini call per group. `"independent"` preserves the historical
per-rollout path for controlled comparisons. Both the HTTP and file-scoring
outputs record the selected order.

Reward ablations use `reward_variant`: `binary`, `whole_coverage`, `base`,
and `full`. They respectively enable binary standalone validation;
all-or-nothing whole-response negative coverage; clause-subset negative
coverage; and coverage plus Shapley credit. The response records both
`reward_variant` and `negative_sampler`, so experiment rows remain auditable.

**HTTP service** (the training interface — see
[docs/training_integration.md](docs/training_integration.md) for the full
turnkey contract):
```bash
python -m rl_pipeline.reward.service --host 0.0.0.0 --port 8000
# generation reward
curl -s localhost:8000/reward -H 'content-type: application/json' \
     -d '{"program":"<C src>","rollouts":[{"invariants":["z==x*y","x>=0"]}]}'
```

Offline JSONL/Parquet groups can be scored with
`python -m rl_pipeline.reward.score_file --input <in> --output <out> --runs 12 --seed 0
--reward-variant full --negative-sampler structured`.
Parquet additionally requires `pandas` and `pyarrow`.

## 3. Inference — `rl_pipeline/inference` (no reward sampling or scoring)

```python
from rl_pipeline.inference import InferenceFramework, VLLMRolloutProvider
inf = InferenceFramework(
    source,
    rollout_provider=VLLMRolloutProvider(model="..."),
)
res = inf.run()   # generate → union → Houdini → Frama-C verify
res.final_invariants, res.verified
```
- **Strict closed-book inference**: generation and Houdini pruning use a
  program with `assert`/`ensures`, executable assertion calls, and non-contract
  comments removed. Only the
  final Frama-C verification inserts the surviving invariants into the original
  target-bearing program.
- CLI: `python -m rl_pipeline.inference --model <hf-or-dir> --inputs '<glob>'`.

## 4. Prompts — `prompt/` (single source of truth)

All static LLM prompt templates live in `prompt/`:
`generate_prompt.txt`, `naive_prompt.txt`, and `system_prompt.txt`.
They are loaded by `rl_pipeline/common/prompts.py`; both Docker images COPY
the directory. Training and inference format the same generation template.

`prompts.system_prompt()` returns this canonical prompt in a fixed rule order
for both training and inference.

## Houdini / Frama-C

The reward filter and inference verify use a **cascade**: lite `PreFramaFilter`
(pure Python syntax/scope filtering, semantic de-duplication, and positive-state
checking) → real inductive **Houdini** via Frama-C/WP + z3. With `frama-c` on
`PATH`, `filters.auto_filter()` resolves to `cascade(pre-frama->houdini)`;
without it, the lite filter remains useful for development, but results are
approximate and are not Frama-C certified; production reward training should
use the image.

---

## Deployment — `deploy/` (Dockerfiles)

| Image | Build (context = repo root) | Runs |
|-------|-----------------------------|------|
| **reward service** | `docker build -f deploy/Dockerfile.reward -t craft-reward .` | `rl_pipeline.reward.service` (gcc + frama-c bundled) |
| **inference** | `docker build -f deploy/Dockerfile.inference -t craft-inference .` | `rl_pipeline.inference` (vLLM + frama-c bundled) |

Both bundle frama-c/z3/why3, so a deployment host needs **no local frama-c**.

## Environment (running natively, no Docker)

- `gcc` (the sampler compiles+runs programs), `z3`, and — for real Houdini/verify
  — `frama-c` + `why3` (e.g. an opam switch: `eval $(opam env --switch=frama-c.27.1)`
  then `why3 config detect`).
- Python deps: `pip install -r deploy/requirements-reward.txt` (FastAPI, Uvicorn,
  Pydantic, NumPy); inference additionally needs `vllm`. Parquet I/O optionally
  needs `pandas` and `pyarrow` (both are included in `environment.yml`).
- `src/config.py` reads the LLM key from `OPENAI_API_KEY` (never hardcode it).

## Repository structure

```
rl_pipeline/          sampler / reward / inference / common
prompt/               ALL LLM prompts (generate / naive / system) — edit here
src/                  reused engine deps (config, llm, houdini_pruner,
                      output_verify, syntax_checker)
                      + input/ (benchmark C programs)
deploy/               Dockerfiles + requirements
docs/                 training_integration.md, local_model_setup.md
paper/                current method description and reproducible evaluation
tests/                standard-library regression tests
unsupported/          exact upstream inputs outside CRAFT's supported model
```

## Benchmark corpora

- `src/input/{linear,NLA_lipus}/` is CRAFT's 366-program canonical sampler
  suite.  It is the suite covered by the discrimination and mislabel-audit
  results reported above and in the paper.
- `src/input/Loopy/` contains the 466 integer programs from Loopy's official
  469-program loop-invariant corpus, normalized to CRAFT's single braced-
  `while`, scalar integer input model. The numeric filename-to-upstream-path
  mapping, checksums, transformations, and source notices are documented in
  [`src/input/Loopy/README.md`](src/input/Loopy/README.md).
- [`unsupported/loopy/`](unsupported/loopy/) preserves exact upstream copies of
  official IDs 353--355. They require floating-point reasoning and are excluded
  from every CRAFT input suite and result.

The Loopy snapshot is a comparison corpus, not a disjoint or held-out set, and
is not part of the measured 366-program sampler audit.  The imported programs
are kept separate so future Loopy results cannot be confused with that existing
measurement.

The two supported corpora also share upstream sources, including Code2Inv, so
`366 + 466` must not be reported as a count of distinct semantic tasks.

## Verification

```bash
python3 -m unittest discover -s tests -v
ruff check rl_pipeline src tests
python3 -m rl_pipeline.eval.discrimination
python3 -m rl_pipeline.eval.mislabel_audit --jobs 8
python3 -m rl_pipeline.eval.mislabel_audit --suite loopy --jobs 8
```

The evaluation commands are slower than the unit tests.  The default mislabel
audit retains the published 366-program core boundary; `--suite loopy` selects
the normalized 466-program comparison corpus. Put the Frama-C/Why3 binaries on
`PATH` to exercise the real cascade.
