# Paired reuse of frozen summaries for loop-exit goals

This supplementary case study uses the existing GPT-5-nano/minimal CRAFT@16
target-hidden and target-visible summaries. Thirty previously selected hidden
strict/exact cases each receive the same eight post-loop goals in both arms.

| Scope | Hidden | Visible |
|---|---:|---:|
| All 240 exit goals | 240 | 209 |
| 223 goals not implied by program context alone | 223 | 192 |
| Programs supporting all eight goals | 30 | 24 |

No model calls, invariant regeneration, Houdini, or Frama-C runs were performed.
These are paired offline entailment checks on an outcome-selected illustrative
case set, not an independent held-out-goal benchmark. Goals may imply each other.
See [REPORT.md](REPORT.md) for scope, semantics, representative cases, and all goals.

All input summaries and sources are copied unchanged in `frozen_inputs/`.
All obligations and counterexamples are retained. With Python and z3-solver
4.13.3, replay the saved queries without the original CRAFT environment:

```sh
python3 scripts/replay_exit_reuse_queries.py --root .
```

The construction script and an unchanged copy of the existing semantic engine
are included under `scripts/`; reconstructing C-to-SMT obligations additionally
requires the CRAFT repository's `rl_pipeline` and pycparser. The construction
script accepts `--output`, `--hidden-root`, and `--visible-root`. Supply an empty
output directory to keep archived results unchanged.
