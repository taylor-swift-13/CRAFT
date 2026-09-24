# CRAFT experimental results

`results/` is the single source of truth for manuscript experiments. Data are
grouped by the research questions in `paper/sections/experiments.tex`; paper
figures read their inputs directly from these directories.

| Directory | Paper scope | Contents |
| --- | --- | --- |
| `01_rq1_main_verification/` | RQ1 | Local/API model evaluations, probe pools, main-table data |
| `02_rq2_tool_comparison/` | RQ2 | AutoSpec, SESpec, Clause2Inv, Loopy, Daikon, and CRAFT tool runs |
| `03_rq3_training_stages/` | RQ3 | SFT composition, RL data, and stage-comparison summaries |
| `04_rq4_ablations/` | RQ4 | Reward, sampler, coverage, clause-cap, and target-visibility experiments |
| `05_appendix_audits/` | Appendix | Data/protocol audits and formal case-study artifacts |

Each section contains a local README. `paper_summaries/` stores compact,
paper-facing tables and plotting inputs; model/run directories retain raw
outputs and manifests. Historical files remain recoverable through Git.

The former `paper/artifacts/` directory has been merged here and removed.
