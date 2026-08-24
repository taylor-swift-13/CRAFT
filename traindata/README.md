# traindata

| file | role | rebuild |
|---|---|---|
| `craft_rl_pool.parquet` | sanitized RL pool (36,742 rows) — the source every derived set starts from | released artifact |
| `craft_sft_pool.json` | sanitized SFT archive (3,096 rows) — source of archival answers | released artifact |
| `craft_generated_rl.json` / `craft_generated_sft.json` | the 1,392 spec-generated programs for under-covered evaluation cells, split per training set (RL merged only its half) | `experiments/generate_cell_programs.py` (nondeterministic; these files are the record) |
| `craft_rl_train.parquet` | **RL training set (5,000 rows)**, difficulty-screened, evaluation-cell matched | see below |
| `craft_sft_train.json` | **SFT training set (14,858 rows)**, pipeline-synthesized targets | see below |

Intermediates (`craft_*_canonical*.parquet/json`, `craft_sft_programs.json`) are deterministic
products of the committed scripts and are not kept in the working tree:

```bash
eval "$(opam env --switch=frama-c.27.1 --set-switch)"
python paper/scripts/canonicalize_training_pool.py rl  --input traindata/craft_rl_pool.parquet  --output traindata/craft_rl_canonical.parquet  --report /tmp/rl_canon.json
python paper/scripts/canonicalize_training_pool.py sft --input traindata/craft_sft_pool.json    --output traindata/craft_sft_canonical.json   --report /tmp/sft_canon.json
python paper/scripts/merge_generated_programs.py --generated traindata/craft_generated_rl.json \
  --pool traindata/craft_rl_canonical.parquet --output traindata/craft_rl_canonical_plus.parquet --report /tmp/merge.json
# (the SFT program pool merges traindata/craft_generated_sft.json instead)
```
The full curation walkthrough (ledger, gates, quotas, SFT synthesis) is `docs/training_integration.md` §0b;
per-stage machine-readable reports live in `paper/artifacts/v4/`.
