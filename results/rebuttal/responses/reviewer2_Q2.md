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
