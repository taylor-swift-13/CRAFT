### Q4 — The motivation for reusable, target-independent invariants is not fully tested. Most experiments check the generated invariants against the original benchmark targets. Appendix B gives examples of reuse, but there is no systematic evaluation on additional goals. Showing that the method works without targets is useful, but does not establish that hiding targets produces more reusable summaries.

Thank you for raising this concern. Our claim is that CRAFT can generate useful, reusable summaries without access to targets; we do not claim that hiding targets necessarily improves reuse. We add a controlled target-visibility comparison and a systematic semantic audit to examine both verification performance and the information retained in the summaries.

We use GPT-5-nano/minimal on the same 832 programs with the same prompt template, generation settings, parser, and verifier. The intended difference is whether generation sees the original target. Each arm uses prefixes of its own fixed 16-response pool:

| Responses | Hidden: verification rate | Visible: verification rate |
|---|---:|---:|
| 1 | 46.39% | 55.17% |
| 4 | 61.66% | 70.31% |
| 8 | 66.71% | 76.08% |
| 16 | 72.36% | 80.77% |

We audit the frozen final @16 invariants with the same semantic checks and proof budgets:

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

Providing targets improves verification of the original goals, reaching 80.77% versus 72.36% at @16. Target-hidden generation nevertheless produces 395 certified strict and 301 certified exact summaries. For each exact case, every valid loop-head state property follows from the effective invariant under the same semantics and context, establishing reusable information beyond the original goal.

On the same 578 jointly verified programs, target-hidden generation has a slightly higher observed rate of certified exact invariants: 50.17% versus 48.27% (290 versus 279). This suggests a favorable trend in retaining complete loop-head state information without targets, alongside the improvement in original-goal verification obtained by providing targets.

We have not completed a separate held-out-goal evaluation within the rebuttal period because constructing and validating multiple meaningful goals independently of the generated invariants requires program-specific work, substantial time, and manual effort. We therefore complement Appendix B with this systematic evidence of the semantic information available for reuse.
