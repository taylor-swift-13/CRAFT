# RL pool selection

The selected data are now published as `traindata/craft_rl.parquet`. They contain
9,024 unchanged rows from `traindata/craft_raw.parquet` (64 × 141). The old RL
file is backed up locally as `craft_rl_previous_4992.parquet` in this archive.
The output is shuffled with seed 20260906. The manifest retains the original
generation filename `craft_rl_10k.parquet`; its content hash also identifies
the renamed final file.

| Property | Old RL | New RL |
|---|---:|---:|
| Rows | 4,992 | 9,024 |
| Families ignoring identifier names and nontrivial constants | 1,725 | 4,196 |
| Maximum rows per such family | 32 | 8 |
| Share of ten largest families | 6.41% | 0.89% |

The new file has no repeated source text. A family retaining constants admits
at most three rows. The static difficulty mix is 529 easy, 7,745 medium, and
750 challenging programs. A variable is counted as active when it occurs in
the loop guard or body; an `if ... break` exit check is excluded from the
decision count. These are complexity proxies, not policy success estimates.

All selected programs produced 60 negative trace groups with the current v9
sampler, 12 runs and seed 0. Selection rejects unsupported/untracked state,
abnormal runs, inadequate negatives, and sampling costs exceeding 15 seconds.
This is a sampler audit, not a proof of useful GRPO reward variance.

The 832 evaluation programs' target-hidden aggregate complexity informed the
final difficulty policy. This is evaluation-informed selection and must be
disclosed if these data are used in reported experiments. No target verdicts,
assertions, per-evaluation-program matching, or SFT answers guide selection.
Selected sources have zero exact or alpha-normalized matches to the inspected
evaluation sources under the script's fingerprints; this does not prove that
all semantic families are disjoint.

The new set remains somewhat more complex in its typical program: median
active variables/decisions/updates are 4/1/3 versus 2/0/2 on the 791 evaluation
programs admitted by the same static eligibility checks. Thus the file covers
the intended complexity range but is not an exact match to the evaluation
difficulty distribution. Actual policy difficulty requires real rollout
statistics. Increased diversity does not guarantee absence of overfitting.

`craft_rl_10k.parquet.manifest.json` records hashes, selection rules, aggregate
comparisons, original pool row indices, and per-output-row provenance.
`craft_rl_10k.parquet.audit.jsonl` retains sampler checks, including unused
candidate checks from earlier selection passes.

Reproduce from the repository root with:

```sh
python3 -m experiments.build_rl_pool_10k --rebuild
```

The builder writes its intermediate output to `traindata/craft_rl_10k.parquet`.
To reuse this archived audit, first copy it to the adjacent default audit path.
Otherwise the builder runs the sampler checks again. The published
`traindata/craft_rl.parquet` is not overwritten by this command.
Sampling timeouts can vary across machines; retain the manifest and audit when
reproducing this exact frozen selection. Use validation performance and group
reward statistics to choose training duration and checkpoints.
