### Q4 — Appendix B provides useful examples of additional-goal verification and downstream summary reuse. Beyond these cases, is there systematic evidence that the same inferred invariants support multiple independently specified held-out goals per program? Such evidence would strengthen the motivation based on reuse for future analyses.

Thank you for this suggestion. We add systematic evidence of what the same inferred summaries can support beyond the original targets. We audit the frozen final invariants from GPT-5-nano/minimal CRAFT@16: among the 602 programs whose original targets were verified, 395 summaries are certified strictly stronger than the original target obligation, and 301 are certified reachability-exact. The inferred invariants are kept fixed throughout the audit.

For comparison, we also audit target-visible generation on the same 832 programs, using the same model, prompt template, generation settings, parser, verifier, semantic checks, and proof budgets. The intended difference is whether generation sees the original target. The @16 results are:

| Program subset | Generation | Programs | Certified strict | Certified exact |
|---|---|---:|---:|---:|
| All verified by this arm | Hidden | 602 | 395 (65.61%) | 301 (50.00%) |
| All verified by this arm | Visible | 672 | 444 (66.07%) | 288 (42.86%) |
| Verified by both arms | Hidden | 578 | 374 (64.71%) | 290 (50.17%) |
| Verified by both arms | Visible | 578 | 380 (65.74%) | 279 (48.27%) |
| Verified only by the hidden arm | Hidden | 24 | 21 (87.50%) | 11 (45.83%) |
| Verified only by the visible arm | Visible | 94 | 64 (68.09%) | 9 (9.57%) |

Percentages use the program count in each row. Each arm consists of the shared 578 verified programs plus its exclusive successes; the shared rows compare the same programs.

The effective invariant combines generated clauses with the applicable entry context, facts about unchanged variables, and type bounds. We use the modeled 32-bit scalar-integer semantics for defined executions and recheck saved SMT obligations with Z3.

Strictness means that, under the same satisfiable context, the invariant implies the target condition translated to the loop head, while the reverse implication has a counterexample.

Exactness means that the effective invariant contains all reachable loop-head states and no unreachable states. We check initialization and preservation, and that every noninitial invariant state has a legal predecessor within the invariant, with an integer measure that decreases backwards and is bounded below. This establishes a finite path back to an initial state.

For each of the 301 certified exact target-hidden cases, every state property valid for all reachable loop-head states follows logically from the same effective invariant under the stated semantics and context. This provides a semantic guarantee for an entire class of additional properties, beyond checking a finite list of goals. The 395 strictness results independently establish that the summaries retain information absent from the original target. These are systematic guarantees of reusable semantic information in the inferred summaries.

We have not completed a separate held-out-goal evaluation within the rebuttal period because constructing and validating multiple meaningful goals independently of the generated invariants requires program-specific work, substantial time, and manual effort. The audit complements Appendix B by establishing the properties these fixed summaries can support under the stated semantics.
