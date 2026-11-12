# Exit-goal reuse on every jointly verified program

All **578** programs verified in both original GPT-5-nano/minimal CRAFT@16 runs
are retained. Selection does not depend on the hidden arm's strict/exact audit.
Source-only template pools are frozen first; correctness is then curated
symmetrically with both frozen summaries. This is not an independent held-out
goal benchmark, and the goals need not be logically independent.

| Jointly resolved targets | Hidden supports | Visible supports |
|---|---:|---:|
| All 3616 paired exit targets | 3533 | 3501 |
| 3440 targets requiring invariant information | 3357 | 3325 |

Eight slots per program were planned. 477 programs have eight confirmed targets;
3,863 correct targets were obtained in total. Of these, 247 remain unconfirmed in
at least one comparison arm. Another 761 slots remain unavailable across 101
programs, including 26 with infeasible exit contexts. All such cases remain in
the inventory, and none are counted as proof failures or vacuous successes.

See [REPORT.md](REPORT.md) for the full coverage, per-arm unresolved counts,
selection rules, semantics, and per-program results. No model generation,
training, Houdini, or Frama-C runs were performed.

Replay archived SMT without the original CRAFT environment:

```sh
python3 scripts/replay_common_exit_reuse.py --root . --workers 4
```

Requires Python and z3-solver 4.13.3. Reconstructing C-to-SMT queries additionally
requires CRAFT's `rl_pipeline`, pycparser, and adapting the historical paths in
the preserved construction scripts. `history/` retains the exact two-stage
candidate-screening records. Original chapter-organized results are unchanged.
