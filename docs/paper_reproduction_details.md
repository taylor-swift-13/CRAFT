# Paper reproduction details

This file records low-level configuration and evaluation provenance omitted
from the manuscript's presentation. Model and sampling hyperparameters remain
in the paper's implementation and experimental-protocol sections.

## Deterministic sampling and data order

- Near-input tier order: `0, 1, 2, -1, 3, 5, 8, -2, 10, 17, 4, -3, 25, 6, 40, 7, -5, 13, 50, 9`.
- Local-fill RNG seed: `seed XOR 0x4C4F4F50`.
- Final RL record shuffle seed: `20260906`; 9,024 records form 141 batches of 64.

## Evaluation provenance

- Single-response-target SFT and Qwen3-8B Bare-initialized reward tables use the September 10, 2026 evaluation report.
- Cross-backbone Whole-rollout controls use the September 10, 2026 report for Qwen3 and the complete two-epoch evaluation for Llama.
- Post-SFT Qwen3-8B Full uses the main evaluation; the other rewards use their reward-ablation evaluations.
- Trained-model verification rates and local cost measurements have separate evaluation sources.

## Training service packaging

The training service uses a self-contained CPU-only container with gcc,
the verifier, Why3, and its prover backend. Inputs support raw model text,
invariant lists, or annotated code; outputs include per-response rewards
and coverage.
