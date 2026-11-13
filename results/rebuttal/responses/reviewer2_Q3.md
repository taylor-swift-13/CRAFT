### Q3 — Does composition still offer a substantial gain when reasoning is enabled under a comparable inference budget?

Thank you for this question. We compare minimal and medium reasoning in CRAFT with GPT-5-nano on the same 832 target-hidden programs:

| Reasoning | CRAFT budget | Verification rate | Tokens/program |
|---|---|---:|---:|
| minimal | compose@16 | 72.36% | 6,840 |
| medium | compose@1 | 58.05% | 6,578 |

At similar recorded token cost, minimal compose@16 achieves 14.30 percentage points higher verification than medium compose@1 with approximately 4% more tokens. The configurations use different system prompts and response batches.

With medium reasoning, increasing the prefix size of the same response pool gives:

| CRAFT (medium reasoning) | Verification rate | Tokens/program | Cumulative API time/program |
|---|---:|---:|---:|
| compose@1 | 58.05% | 6,578 | 48.7s |
| compose@4 | 76.92% | 26,271 | 189.8s |
| compose@8 | 80.41% | 54,687 | 377.6s |

Verification improves by 18.87 points from one to four responses and 22.36 points from one to eight, showing continued gains from composition with stronger reasoning. Costs sum input/output tokens and API call durations.
