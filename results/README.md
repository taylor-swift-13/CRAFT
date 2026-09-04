# Final GPT-5-nano results

Only the final 832-task evaluation is retained:

- `gpt5nano_full832/final_report.md`: readable seven-method comparison.
- `gpt5nano_full832/summary.csv`: method and suite aggregates.
- `gpt5nano_full832/final_results.csv`: compact 5,824-row result table.
- `gpt5nano_full832/efficiency_reaudit.md`: corrected wall-time and token table.
- `gpt5nano_full832/efficiency_batches.csv`: batch/session timing evidence.
- `gpt5nano_full832/methods/`: one final directory per method.

## Per-method results

Each directory contains `results.jsonl`, `results.csv`, `summary.csv`, and
`summary.json`:

- `gpt5nano_full832/methods/autospec/`
- `gpt5nano_full832/methods/clause2inv/`
- `gpt5nano_full832/methods/sespec/`
- `gpt5nano_full832/methods/naive/`
- `gpt5nano_full832/methods/loopgym_r1_no_houdini/`
- `gpt5nano_full832/methods/loopgym_r1_houdini/`
- `gpt5nano_full832/methods/loopgym_r4_houdini/`

Where raw generated artifacts are available, the method directory contains an
`artifacts` link into the canonical artifact tree.

The fixed samples, raw event streams, and protocol manifests remain inside
`gpt5nano_full832/` because they are required to reproduce the final scores.
Legacy, target-leaking, and superseded intermediate batches have been removed.
