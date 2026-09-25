# Baseline failing multi-goal case: Loopy/202

The original benchmark target is `i == j` after a loop that alternates
increments between two counters. CRAFT's eight-response target-hidden
GPT-5-nano result is verified in the frozen comparison; AutoSpec, SESpec,
Naive, and Daikon fail, and Clause2Inv does not support this Loopy task.

`run.py` takes the first eight responses from the saved ten-response pool,
checks the verified `k=8` row in `grid_recompute.jsonl`, and selects four
clauses present in that prefix. Together they prove the original target and
three extra targets:
conservation of `i+j`, and the exact progress of each counter relative to
the initial `j`. The extra targets were written after generation. The script
also replays the archived AutoSpec, Naive, and Daikon annotations against
the same four assertions, both jointly and in separate runs for each goal.
It checks every named assertion and all WP proof obligations, including
induction. CRAFT verifies all four independent targets; the three replayed
baselines verify none. Naive's original assertion is locally proved, but its
candidate invariant is not inductive.

Reproduce from the repository root with Frama-C 31, Alt-Ergo, and Z3:

```bash
python3 results/05_appendix_audits/formal_cases/multi_goal_gpt5nano_loopy202/run.py
```

Set `FRAMA_C=/path/to/frama-c` if needed. `result.json` records the source
paths, archived verdicts, clause sets, and proof counts. Running the script
generates annotated `.c` files and `.log` files for each WP check.
