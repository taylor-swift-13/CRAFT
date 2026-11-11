# ICLR 2027 Rebuttal

**Paper:** Clause-Compositional Inference and Aligned Training for Target-Hidden Loop Verification

## Reviewer 1

### Q1 — Can you clarify the candidate-generation, selection and verification budgets for SFT (single) and composed-label SFT? Under a common candidate pool and matched budgets, do composed labels still outperform selecting a single independently filtered response?

Thank you for suggesting this comparison. We first clarify the original SFT data construction, then present a supplementary controlled comparison.

Both SFT datasets use the same input-program pool, Qwen3-8B generator, and training settings, with approximately equal sizes. Over 93% of the distinct prompts in the composed-label dataset also occur in the single-response dataset.

In the original SFT ablation, construction budgets differ: SFT (single) filters one response and skips examples with no valid clauses. Composed-label SFT jointly filters 16 responses per round for at most five rounds, accepting labels at negative coverage >= 0.5; SFT (single) has no such threshold. The 63.94% versus 23.20% compose@1 result compares these complete construction procedures.

For the supplementary experiment, we compare highest-coverage selection and composition on all 466 Loopy programs using exactly the same candidate pool. Both methods reuse the same first N GPT-5-nano/minimal responses, matching LLM generation costs in model-call counts and token usage. Both methods share the parser and Houdini settings.

With this fixed candidate pool, selecting the best single response requires N independent filtering runs, whereas composition filters the clause union jointly. The verification workloads therefore differ by construction; their CPU-cost ratio depends on N and the proof workload. Highest-coverage selection chooses an independently filtered response using shared, frozen negative traces. Targets are hidden during selection and restored for evaluation. "Any single succeeds" counts a program if any independently filtered response proves its target.

| Responses N | Highest-coverage single | Any single succeeds | Composition |
|---|---:|---:|---:|
| 1 | 47.85% | 47.85% | 47.85% |
| 4 | 56.87% | 61.16% | 63.09% |
| 8 | 60.30% | 65.88% | 69.10% |
| 16 | 64.16% | 71.46% | 74.89% |

At N=16, composition verifies 50 more programs than highest-coverage selection (+10.73 percentage points), including 16 programs that no individual filtered response verified under the tested configuration (+3.43 points over "Any single succeeds"). At N=4/8/16, composition also retains every success achieved by an individual response.

Recorded cumulative filtering times are:

| Responses N | Independent filtering (hours) | Joint filtering (hours) |
|---|---:|---:|
| 1 | 2.83 | 2.83 |
| 4 | 13.11 | 8.48 |
| 8 | 26.37 | 13.55 |
| 16 | 52.76 | 22.55 |

At N=16, joint filtering reduces recorded cumulative time by 57.3% while producing higher-quality candidates. Appendix K.6 (Table 21) provides further evidence of lower filtering cost.

Our SFT dataset contains 31,865 distinct inputs. A rough extrapolation of these per-input timings gives about 3,608 versus 1,542 cumulative hours for one N=16 construction round. Together with generation, additional rounds, paired SFT retraining, and evaluation, this exceeded our available time and compute during the two-week rebuttal period. We apologize that we could not complete the paired training comparison; the same-pool results directly support the value of composition for label construction.

### Q2 — How does Loopy perform after correcting the invariant-extraction bottleneck while keeping its remaining procedure and budget unchanged?

Thank you for asking us to examine the extraction behavior. Loopy's extractor requires paired Markdown fences, so unfenced ACSL annotations were dropped before Houdini. After fixing the extractor, Loopy achieves a verification rate of 68.03%.

Our core innovation is training aligned with clause composition to improve compositional search efficiency. CRAFT's original inference framework used a relatively simple prompt.

The supplementary GPT-5-nano/minimal run uses a stronger prompt with explicit ACSL syntax examples and constraints. The following supplementary comparison covers all 832 programs:

| Method | Rate | Tokens/program |
|---|---:|---:|
| CRAFT @1 | 46.39% | 1,268 (est.) |
| CRAFT @4 | 61.66% | 1,703 |
| CRAFT @8 | 66.71% | 3,421 |
| CRAFT @16 | 72.36% | 6,840 |
| CRAFT trained @1 (Qwen3-8B + SFT + RL) | 69.23% | 1,348 |
| Loopy, corrected initial composition (15 responses) | 66.47% | 6,904 |
| Loopy, corrected initial + repairs (15 initial + up to 7 repair responses) | 68.03% | 10,016 |

CRAFT uses the new GPT-5-nano/minimal batch, except for the trained Qwen3-8B row. Loopy's costs cover the cached responses accessed during re-verification.

At nearly equal recorded token usage, CRAFT@16 reaches 72.36% versus 66.47% for Loopy's initial composition. It also exceeds Loopy including repairs by 4.33 percentage points with 31.7% fewer recorded tokens. In this target-hidden evaluation, the existing repair responses add 13 programs (1.56 points) beyond initial composition; their incremental benefit is much smaller than that of composition.

With training aligned with the inference framework, Qwen3-8B's compose@1 reaches 63.94% after SFT and further improves to 69.23% after RL, exceeding compose@32 (57.81%) of the model before our SFT/RL training while requiring only a single response.

### Q3 — What practical circumstances motivate withholding targets during training as well as inference? A multi-goal verification reward would introduce additional supervision and therefore change the setting, but a comparison or discussion could help clarify the practical trade-off of fully target-independent training.

Thank you for this suggestion. A practical example is legacy code without accompanying formal specifications, such as assertions or postconditions. Here, a useful task is property discovery: finding and verifying properties of the code before a particular verification goal has been supplied. CRAFT produces verified loop summaries that, together with exit conditions and program context, can help establish properties after the loop terminates. These summaries can support later analyses and specification writing.

The reason for withholding targets during training is the difficulty and cost of preparing them for a large training corpus. This requires understanding programs, writing suitable specifications, and checking that those specifications are correct and meaningful. Our training procedure can use code without first adding these goals. In our experiments, we withhold existing targets to test whether the method can learn and generate useful invariants without relying on them; the targets are used for evaluation.

Beyond the preparation cost discussed above, goal quality also determines the usefulness of target-based supervision. Suitable existing goals can naturally provide additional direct supervision, but deriving correct, informative goals from source code requires discovering useful program properties and validating them. This difficulty is central to the problem we study and motivates our target-hidden setting.

We train on code without prewritten verification specifications using inductiveness checks to verify that the retained clauses jointly form a valid loop invariant, and negative-trace coverage as a target-independent strength signal for SFT-label construction and RL rewards.

Our supplementary inference comparison illustrates the benefit of available goals: with GPT-5-nano/minimal and 16 responses on the same 832 programs, target-visible generation verifies 80.77%, versus 72.36% with targets hidden.

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

### Q5 — Are the post-SFT gains from Shapley credit consistent across training seeds? Table 27 reports improvements of 1.32 percentage points in compose@1 and 1.68 points in compose@32, but the stability of these incremental gains is not established by the reported experiments.

Thank you for asking about stability. Three independent training runs with different seeds and the same training and evaluation settings show small positive Shapley gains. Each compares Full with the corresponding clause-decomposed reward without Shapley:

| Repetition | Shapley gain in compose@1 (pp) | Shapley gain in compose@32 (pp) |
|---|---:|---:|
| 1 | +0.6 | +0.1 |
| 2 | +1.8 | +1.7 |
| Reported in Table 27 | +1.3 | +1.7 |

The modest gains after SFT are consistent with our expectations and mechanism. Our SFT data construction collects multiple responses, jointly filters their clauses, and selects composed labels using negative-trace coverage. It substantially improves compositional verification with few responses: compose@1 reaches 63.94% after SFT, exceeding compose@32 before SFT (57.81%). Thus, SFT makes a fixed sampling budget more effective.

Our method for constructing discriminative negative traces also provides an informative signal for clause-level rewards. Effective SFT and this reward signal make a smaller marginal benefit from additional credit assignment plausible. At each fixed sampling budget, Table 27 shows that Shapley gains decrease after SFT: from 3.00 to 1.32 percentage points at compose@1 and from 4.32 to 1.68 points at compose@32, consistent with this explanation.

For context, Appendix K.10 (Figure 12) analyzes distribution sharpening under whole-response RL: pass@1 rises from 6.37% to 15.50%, while pass@32 falls from 25.96% to 18.39%. Our clause-decomposed training and compositional inference mitigate this coverage loss.

## Reviewer 2 — AjZF

### Q1 — Can you compare composition and iterative repair using the same model, parser, verifier, and token or time budget? Does the advantage remain when both methods have access to targets and full verifier feedback?

Thank you for suggesting this comparison. We compare composition and vanilla iterative repair on all 466 Loopy programs using GPT-5-nano/minimal, the same parser and verifier settings, and Houdini filtering. This is an independent experiment with a simple shared initial prompt and a separate response batch from the CRAFT@16 pool used in the label-construction comparison. Within each target-visibility setting, both methods share the initial response and use exactly n model calls, one response per call, for n = 1, 4, 8.

Composition pools independent responses; repair makes sequential revisions using the previous candidate and full raw verifier feedback. Both use the same initial prompt and all n calls. Visible targets are supplied to both methods and included in repair feedback; hidden targets are restored only for evaluation. Houdini is applied separately for candidate evaluation.

We report cumulative success: a program is solved at budget n if any iteration from 1 through n succeeds. At each iteration, Houdini evaluates the current repair answer or the union of composition responses generated so far.

| Setting | Strategy | @1 | @4 | @8 | Tokens/program @8 |
|---|---|---:|---:|---:|---:|
| Hidden | Composition | 46.57% | 59.44% | 65.24% | 2,464 |
| Hidden | Iterative repair | 46.57% | 51.50% | 54.08% | 13,951 |
| Visible | Composition | 53.22% | 68.03% | 74.03% | 2,657 |
| Visible | Iterative repair | 53.22% | 58.80% | 60.52% | 14,144 |

At eight calls, the composition-minus-repair verification differences are 11.16 percentage points with hidden targets and 13.52 points with visible targets. Repair consumes approximately 5.7 and 5.3 times as many tokens, respectively.

Both methods execute all n calls, and the reported token costs include all n calls.

### Q2 — How much of Loopy’s poor performance is due to its extractor? What happens if its generated annotations are processed by CRAFT’s parser?

Thank you for asking us to examine the extraction behavior. Loopy's extractor requires paired Markdown fences, so unfenced ACSL annotations were dropped before Houdini. After fixing the extractor, Loopy achieves a verification rate of 68.03%.

Our core innovation is training aligned with clause composition to improve compositional search efficiency. CRAFT's original inference framework used a relatively simple prompt.

The supplementary GPT-5-nano/minimal run uses a stronger prompt with explicit ACSL syntax examples and constraints. The following supplementary comparison covers all 832 programs:

| Method | Rate | Tokens/program |
|---|---:|---:|
| CRAFT @1 | 46.39% | 1,268 |
| CRAFT @4 | 61.66% | 1,703 |
| CRAFT @8 | 66.71% | 3,421 |
| CRAFT @16 | 72.36% | 6,840 |
| CRAFT trained @1 (Qwen3-8B + SFT + RL) | 69.23% | 1,348 |
| Loopy, corrected initial composition (15 responses) | 66.47% | 6,904 |
| Loopy, corrected initial + repairs (15 initial + up to 7 repair responses) | 68.03% | 10,016 |

CRAFT uses the new GPT-5-nano/minimal batch, except for the trained Qwen3-8B row. Loopy's costs cover the cached responses accessed during re-verification.

At nearly equal recorded token usage, CRAFT@16 reaches 72.36% versus 66.47% for Loopy's initial composition. It also exceeds Loopy including repairs by 4.33 percentage points with 31.7% fewer recorded tokens. In this target-hidden evaluation, the existing repair responses add 13 programs (1.56 points) beyond initial composition; their incremental benefit is much smaller than that of composition.

With training aligned with the inference framework, Qwen3-8B's compose@1 reaches 63.94% after SFT and further improves to 69.23% after RL, exceeding compose@32 (57.81%) of the model before our SFT/RL training while requiring only a single response.

### Q3 — Does composition still offer a substantial gain when reasoning is enabled under a comparable inference budget?

Thank you for this question. We compare CRAFT with GPT-5-nano at minimal and medium reasoning on the same 832 target-hidden programs. At similar recorded token costs:

| Reasoning | CRAFT budget | Verification rate | Tokens/program |
|---|---|---:|---:|
| minimal | compose@16 | 72.36% | 6,840 |
| medium | compose@1 | 58.05% | 6,578 |

At similar recorded token cost, minimal compose@16 achieves 14.30 percentage points higher verification than medium compose@1 with approximately 4% more tokens. The configurations use different system prompts and response batches.

Within the medium configuration, prefixes of the same response pool show the following sampling-budget trend:

| CRAFT (medium reasoning) | Verification rate | Tokens/program | Cumulative API time/program |
|---|---:|---:|---:|
| compose@1 | 58.05% | 6,578 | 48.7s |
| compose@4 | 76.92% | 26,271 | 189.8s |
| compose@8 | 80.41% | 54,687 | 377.6s |

Verification improves by 18.87 points from one to four responses and 22.36 points from one to eight, showing continued gains from composition with stronger reasoning. Costs sum input/output tokens and API call durations.

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

## Reviewer 3 — yCpd

### Q1 — Could you compare, on the same batch of training programs and with the same candidate generation budget, "selecting the single response with the highest coverage as the label" against "composing multiple responses as the label"? This would more directly show how much of the SFT improvement comes from composition itself.

Thank you for suggesting this comparison. We first clarify the original SFT data construction, then present a supplementary controlled comparison.

Both SFT datasets use the same input-program pool, Qwen3-8B generator, and training settings, with approximately equal sizes. Over 93% of the distinct prompts in the composed-label dataset also occur in the single-response dataset.

In the original SFT ablation, construction budgets differ: SFT (single) filters one response and skips examples with no valid clauses. Composed-label SFT jointly filters 16 responses per round for at most five rounds, accepting labels at negative coverage >= 0.5; SFT (single) has no such threshold. The 63.94% versus 23.20% compose@1 result compares these complete construction procedures.

For the supplementary experiment, we compare highest-coverage selection and composition on all 466 Loopy programs using exactly the same candidate pool. Both methods reuse the same first N GPT-5-nano/minimal responses, matching LLM generation costs in model-call counts and token usage. Both methods share the parser and Houdini settings.

With this fixed candidate pool, selecting the best single response requires N independent filtering runs, whereas composition filters the clause union jointly. The verification workloads therefore differ by construction; their CPU-cost ratio depends on N and the proof workload. Highest-coverage selection chooses an independently filtered response using shared, frozen negative traces. Targets are hidden during selection and restored for evaluation. "Any single succeeds" counts a program if any independently filtered response proves its target.

| Responses N | Highest-coverage single | Any single succeeds | Composition |
|---|---:|---:|---:|
| 1 | 47.85% | 47.85% | 47.85% |
| 4 | 56.87% | 61.16% | 63.09% |
| 8 | 60.30% | 65.88% | 69.10% |
| 16 | 64.16% | 71.46% | 74.89% |

At N=16, composition verifies 50 more programs than highest-coverage selection (+10.73 percentage points), including 16 programs that no individual filtered response verified under the tested configuration (+3.43 points over "Any single succeeds"). At N=4/8/16, composition also retains every success achieved by an individual response.

Recorded cumulative filtering times are:

| Responses N | Independent filtering (hours) | Joint filtering (hours) |
|---|---:|---:|
| 1 | 2.83 | 2.83 |
| 4 | 13.11 | 8.48 |
| 8 | 26.37 | 13.55 |
| 16 | 52.76 | 22.55 |

At N=16, joint filtering reduces recorded cumulative time by 57.3% while producing higher-quality candidates. Appendix K.6 (Table 21) provides further evidence of lower filtering cost.

Our SFT dataset contains 31,865 distinct inputs. A rough extrapolation of these per-input timings gives about 3,608 versus 1,542 cumulative hours for one N=16 construction round. Together with generation, additional rounds, paired SFT retraining, and evaluation, this exceeded our available time and compute during the two-week rebuttal period. We apologize that we could not complete the paired training comparison; the same-pool results directly support the value of composition for label construction.

### Q2 — Why do most of Loopy's responses fail to reach Houdini? How much would the results change after fixing the format adapter or using a shared extractor? I do not think the baseline needs to be redesigned, but the pervasiveness of parsing failures undermines the persuasiveness of the current comparison.

Thank you for asking us to examine the extraction behavior. Loopy's extractor requires paired Markdown fences, so unfenced ACSL annotations were dropped before Houdini. After fixing the extractor, Loopy achieves a verification rate of 68.03%.

Our core innovation is training aligned with clause composition to improve compositional search efficiency. CRAFT's original inference framework used a relatively simple prompt.

The supplementary GPT-5-nano/minimal run uses a stronger prompt with explicit ACSL syntax examples and constraints. The following supplementary comparison covers all 832 programs:

| Method | Rate | Tokens/program |
|---|---:|---:|
| CRAFT @1 | 46.39% | 1,268 |
| CRAFT @4 | 61.66% | 1,703 |
| CRAFT @8 | 66.71% | 3,421 |
| CRAFT @16 | 72.36% | 6,840 |
| CRAFT trained @1 (Qwen3-8B + SFT + RL) | 69.23% | 1,348 |
| Loopy, corrected initial composition (15 responses) | 66.47% | 6,904 |
| Loopy, corrected initial + repairs (15 initial + up to 7 repair responses) | 68.03% | 10,016 |

CRAFT uses the new GPT-5-nano/minimal batch, except for the trained Qwen3-8B row. Loopy's costs cover the cached responses accessed during re-verification.

At nearly equal recorded token usage, CRAFT@16 reaches 72.36% versus 66.47% for Loopy's initial composition. It also exceeds Loopy including repairs by 4.33 percentage points with 31.7% fewer recorded tokens. In this target-hidden evaluation, the existing repair responses add 13 programs (1.56 points) beyond initial composition; their incremental benefit is much smaller than that of composition.

With training aligned with the inference framework, Qwen3-8B's compose@1 reaches 63.94% after SFT and further improves to 69.23% after RL, exceeding compose@32 (57.81%) of the model before our SFT/RL training while requiring only a single response.

### Q3 — There are two reporting points I find somewhat confusing and would like clarified.

#### Q3.1 — Second, in Table 23, GPT-5-nano's pass@1 is 3.80% on Linear and 3.86% on Loopy, whereas in Table 25 it is 2.53% and 4.72%; both tables report an overall value of 3.61%, and the text states that the same sample pool is used. Is this a typo in one of the tables, or do the two evaluations differ in how they are processed?

Thank you for identifying this reporting error. The GPT-5-nano pass@1 results in Tables 23 and 25 come from two independent runs. We will correct the shared-pool description and use the first run, underlying Table 23, as the common reporting source for both tables.

#### Q3.2 — First, does the SFT (single) in K.2 also use the same coverage threshold as the compositional labels? Does "approximately the same number of training samples" mean that both groups use the same batch of programs?

Thank you for requesting this clarification. SFT (single) does not use the same coverage threshold. Both datasets start from exactly the same input-loop pool and use the same SFT settings. Composed labels use multi-round composition, filtering, and coverage-based acceptance; SFT (single) generates one response per loop, removes incorrect clauses, and skips examples with none remaining.

"Approximately the same number of training samples" refers to the resulting dataset sizes. The procedures retain slightly different inputs: over 93% of the distinct prompts in the composed-label dataset also appear in the single-response dataset. We will clarify this distinction between the original program pool and the retained training sets in K.2.


### Q4 — Moreover, the trained 8B model still reaches only 12% on the nonlinear subset, indicating that the overall improvement does not yet extend to such difficult tasks.

Thank you for highlighting the limited performance on the nonlinear subset. Before our training, Qwen3-8B achieves only 10% even when composing 32 responses; after training aligned with compositional inference, it reaches 12% with a single response. In comparison, the same CRAFT framework with GPT-5-nano/minimal achieves 34% at compose@1 and 44% at compose@16 on this subset.

Our training relies on model-generated candidates. When these candidates lack the necessary nonlinear relations, both the information available for composition and the useful supervision available for training are limited. These results are consistent with the explanation that the current model struggles to generate the required nonlinear relations. They also show that CRAFT can use candidates from a stronger model to solve more nonlinear tasks. Using models with stronger nonlinear reasoning capabilities is an important direction for further improvement.

### Q5 — The additional benefit of the Shapley term remains uncertain. After SFT, it raises compose@1 from 67.91% to 69.23%, a gain clearly smaller than that brought by the clause reward itself. In the absence of repeated training runs, I would be cautious about the conclusion that it reliably improves exploration.

Thank you for asking about stability. Three independent training runs with different seeds and the same training and evaluation settings show small positive Shapley gains. Each compares Full with the corresponding clause-decomposed reward without Shapley:

| Repetition | Shapley gain in compose@1 (pp) | Shapley gain in compose@32 (pp) |
|---|---:|---:|
| 1 | +0.6 | +0.1 |
| 2 | +1.8 | +1.7 |
| Reported in Table 27 | +1.3 | +1.7 |

The modest gains after SFT are consistent with our expectations and mechanism. Our SFT data construction collects multiple responses, jointly filters their clauses, and selects composed labels using negative-trace coverage. It substantially improves compositional verification with few responses: compose@1 reaches 63.94% after SFT, exceeding compose@32 before SFT (57.81%). Thus, SFT makes a fixed sampling budget more effective.

Our method for constructing discriminative negative traces also provides an informative signal for clause-level rewards. Effective SFT and this reward signal make a smaller marginal benefit from additional credit assignment plausible. At each fixed sampling budget, Table 27 shows that Shapley gains decrease after SFT: from 3.00 to 1.32 percentage points at compose@1 and from 4.32 to 1.68 points at compose@32, consistent with this explanation.

For context, Appendix K.10 (Figure 12) analyzes distribution sharpening under whole-response RL: pass@1 rises from 6.37% to 15.50%, while pass@32 falls from 25.96% to 18.39%. Our clause-decomposed training and compositional inference mitigate this coverage loss.
