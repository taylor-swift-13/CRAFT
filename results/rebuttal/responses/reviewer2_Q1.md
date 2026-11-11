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
