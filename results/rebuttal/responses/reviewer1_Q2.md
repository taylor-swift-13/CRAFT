### Q2 — How does Loopy perform after correcting the invariant-extraction bottleneck while keeping its remaining procedure and budget unchanged?

Thank you for highlighting the extraction issue. Loopy's extractor dropped ACSL annotations without paired Markdown fences before Houdini. Correcting this raises its verification rate to 68.03%.

Our core contribution is composition-aligned training to improve search efficiency. The original inference prompt was relatively simple; the supplementary GPT-5-nano/minimal evaluation on all 832 programs uses a prompt aligned with Loopy, including explicit ACSL syntax examples and constraints:

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

With composition-aligned training, Qwen3-8B reaches 63.94% compose@1 after SFT and 69.23% after RL, both exceeding its compose@32 of 57.81% before our SFT/RL training, using just one response.
