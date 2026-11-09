# Illustrative completed case: Loopy 18

This illustrates one case from the completed 466-program experiment. Full-population results are in `comparison_report.md`.

The original program requires `N > 1`, initializes `i = j = k = 0`, and executes:

```c
while (i < N) {
    if (i < 1) k = 1;
    else k = 0;
    j++;
    i++;
}
//@ assert(!(k > 0));
```

Response 2 independently retains `k <= 1`. Response 4 independently retains
`k == 1 ==> i <= 1 && j <= i`. Response 4 does not retain the bound `k <= 1`.

At loop exit, `i >= N > 1`. The conditional relation therefore excludes `k == 1`; together with `k <= 1`, this establishes `k <= 0`, the original target. These two useful constraints come from different responses.

| Prefix budget | Highest-coverage single response proves target | Any independently filtered response proves target | Joint composition proves target |
|---|---|---|---|
| 1 | No | No | No |
| 4 | No | No | Yes |
| 8 | No | No | Yes |
| 16 | No | No | Yes |

At N=4, the selected single response is response 4. Its negative-trace coverage is 59/60, exactly the same as the composition's coverage. It retains 7 clauses, while the composition retains 19. The verification distinction therefore exists even when the sampled coverage proxy is tied.

The verification results use the common 5-second-per-goal Frama-C/WP configuration. They describe what the tested verifier establishes, not a general impossibility theorem about alternative proof procedures. No model generation or training was used for this analysis.

Evidence: `per_program/Loopy_18.json` in the independent run directory, the frozen original response pool, the independently filtered candidate records, and the existing composed-prefix proof logs referenced by `protocol.json`.
