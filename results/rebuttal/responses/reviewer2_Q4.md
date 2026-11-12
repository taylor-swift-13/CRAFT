### Q4 — The motivation for reusable, target-independent invariants is not fully tested. Most experiments check the generated invariants against the original benchmark targets. Appendix B gives examples of reuse, but there is no systematic evaluation on additional goals. Showing that the method works without targets is useful, but does not establish that hiding targets produces more reusable summaries.

Thank you for raising this question. Our motivation is to generate useful, reusable invariants when verification targets are not yet available. We supplement Appendix B with a target-visibility comparison, a semantic audit, and an additional-goal evaluation.

We use GPT-5-nano/minimal on the same 832 programs, with the same prompt template, generation settings, parser, and verifier. The intended difference is whether generation sees the original target. Each setting uses prefixes of its own fixed 16-response pool:

| Responses | Target-hidden | Target-visible |
|---|---:|---:|
| 1 | 46.39% | 55.17% |
| 4 | 61.66% | 70.31% |
| 8 | 66.71% | 76.08% |
| 16 | 72.36% | 80.77% |

We then audit the frozen @16 invariants using the same semantic checks and proof budgets:

| Program subset | Setting | Programs | Certified strict | Certified exact |
|---|---|---:|---:|---:|
| Verified in this setting | Hidden | 602 | 395 (65.61%) | 301 (50.00%) |
| Verified in this setting | Visible | 672 | 444 (66.07%) | 288 (42.86%) |
| Verified in both settings | Hidden | 578 | 374 (64.71%) | 290 (50.17%) |
| Verified in both settings | Visible | 578 | 380 (65.74%) | 279 (48.27%) |

Percentages use the program count in each row. The shared rows compare exactly the same programs.

The audit combines generated clauses with entry conditions, facts about unchanged variables, and type bounds under the modeled, defined 32-bit scalar-integer semantics. For **strictness**, SMT checks establish that the invariant implies the original target translated to the loop head, while the reverse implication has a counterexample under the same satisfiable context. For **exactness**, we prove that the invariant describes exactly the set of reachable loop-head states.

Thus, the strict summaries retain information absent from the original target. For each exact summary, every property valid for all reachable loop-head states follows logically from that summary under the stated semantics and context.

We also constructed eight additional post-loop goals for each of **446 programs whose original targets were successfully verified in both settings**, and compared how many goals could be proved using the existing target-hidden and target-visible invariants. The goals came from fixed source-based templates covering bounds, input–output relations, parity, and other arithmetic properties. Both sets of invariants were used symmetrically to check candidate correctness, followed by selection in a fixed order without favoring either setting. Each program used exactly the same eight goals in both settings.

| Metric | Target-hidden | Target-visible |
|---|---:|---:|
| Goals proved | 3,487/3,568 (97.73%) | 3,461/3,568 (97.00%) |
| Programs proving all eight goals | 424/446 (95.07%) | 411/446 (92.15%) |

Providing targets improves verification of the original goals. Meanwhile, the semantic audit shows that target-hidden invariants retain substantial information beyond those goals, and the additional-goal evaluation demonstrates reuse of the same invariants for multiple post-loop properties. The two settings perform similarly on these additional goals, with target-hidden invariants proving all eight goals on 13 more programs. Together, these results support CRAFT’s ability to produce reusable summaries without access to verification targets.
