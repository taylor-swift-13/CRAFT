# GPT-5-nano full-832 reproduction

This directory is the executable protocol for comparing five methods on the
same 832 LoopGym programs:

1. AutoSpec
2. Clause2Inv
3. SESpec
4. LoopGym
5. Naive direct generation

The authoritative settings are in `protocol.json`. `protocol_sha256` is stored
in every result row, so results from different prompts, budgets, or target
boundaries cannot be silently mixed.

## Information boundary

Every model-facing program is produced by
`rl_pipeline.common.program.strip_postcondition`:

- ACSL `assert` and `ensures` predicates are removed;
- executable `assert(...)` and `__VERIFIER_assert(...)` calls are removed;
- `requires` clauses and loop semantics are retained;
- a conventional `ERROR:` label is renamed together with its gotos so the
  label text does not reveal the target.

The hidden source is saved with each fresh run. Final success is never taken
from the hidden program. Generated loop invariants are inserted into the
untouched original source and judged by the common Frama-C/WP gate.

The evaluation traces are also fixed artifacts, not resampled per method.
`samples/<suite>/<case>.json.gz` stores every positive state, negative witness
state, and negative trace grouping. `samples_manifest.jsonl` binds each file to
the hidden-source hash, `n_runs=12`, `seed=0`, state counts, and both compressed
and uncompressed content hashes.
The `score` command only reads these artifacts and fails if the frozen
832-task manifest is missing or no longer matches the source files.
Deterministic sampler execution failures are also frozen as explicit failed
artifacts; they are never silently converted into a zero-negative sample.
Concrete inputs that terminate abnormally because the benchmark itself reaches
undefined C behavior are skipped and recorded in `stats.skipped_abnormal_runs`;
the other valid executions for that task remain part of the fixed sample.

## Per-task result schema

Each JSONL row is keyed by:

```text
(method, suite, case_id, model, protocol_sha256)
```

and contains:

- original and hidden source SHA-256;
- raw model responses and extracted/generated invariants when available;
- native tool status and common `verified` result;
- `generation_seconds`, `judge_seconds`, `negative_score_seconds`, and
  `reproduction_total_seconds`;
- prompt, completion, and total tokens, API call count, and whether token
  accounting is `exact`, `estimated`, `unavailable`, or `not_called`;
- sampled positive/negative counts, rejected negative count, and
  `negative_rejection_score`;
- for programs where conservative sampling produces no negatives,
  `negative_rejection_score=null` and the separate
  `binary_frama_c_validation` field.

Failures and unsupported inputs remain rows; the denominator is always 832.

## Reuse policy

The `import-existing` command imports an existing result only when its method, model,
source hash, and target-hidden boundary are compatible with this protocol.
Imported artifacts are rejudged and rescored, but the model is not called
again. Missing exact token usage is never fabricated: recoverable log-based
counts are marked `estimated`; otherwise the field is `null`.
The two legacy inference batches expose only batch-level wall time. Their
sanitized timing/configuration provenance is stored in `legacy_batches.json`;
per-task time remains `null` rather than being invented.

The existing AutoSpec “366 strict” batch is intentionally ineligible because
its command logs show the original assertion in the model-facing source.
The existing SESpec “strict2” batch is retained as audit evidence but is also
ineligible: its wrapper edited `SESPEC_INPUT_ROOT`, while `main.py` read the
separate `SESpec/src/input` tree that still contained the assertion.

Native Clause2Inv needs a Code2Inv SMT transition-VC file for each input.
Compatible VCs exist for the 316 linear and 50 nonlinear programs, so those
target-hidden generations are reused. Loopy has no such VCs; the runner writes
an explicit `unsupported` row with zero API calls instead of silently changing
Clause2Inv into a different method or spending tokens on unverifiable output.

## Commands

Build and audit the task manifest:

```bash
python -m experiments.gpt5nano_full832.run manifest
```

Materialize or validate the fixed 832-task evaluation sample set:

```bash
python -m experiments.gpt5nano_full832.run samples --workers 8
```

Import the reusable 366-task Naive, LoopGym, and Clause2Inv generations and
retain incompatible SESpec rows for audit:

```bash
python -m experiments.gpt5nano_full832.run import-existing
```

Run only missing model calls (requires `OPENAI_API_KEY`; all commands resume):

```bash
python -m experiments.gpt5nano_full832.run generate --method naive --workers 8
python -m experiments.gpt5nano_full832.run generate --method loopgym --workers 8
python -m experiments.gpt5nano_full832.run generate --method autospec --workers 8 --timeout 600
python -m experiments.gpt5nano_full832.run generate --method sespec --workers 8
python -m experiments.gpt5nano_full832.run generate --method clause2inv --workers 8
```

Or resume the complete import → missing generation → common score → summary
workflow with one command:

```bash
python -m experiments.gpt5nano_full832.run all --workers 8 --score-workers 4
```

Apply the common final judge and negative-rejection scorer:

```bash
python -m experiments.gpt5nano_full832.run score --workers 10
python -m experiments.gpt5nano_full832.run summarize
python -m experiments.gpt5nano_full832.report
```

The completed run used 10 scoring workers. The score command validates the
frozen sample manifest before use and automatically recomputes an imported
negative score when its sample hash or positive/negative counts do not match
the frozen sample. Archived schema-v1 samples remain readable after the
schema-v2 writer change; new sample generation still writes schema v2.

Outputs default to `results/gpt5nano_full832/`. JSONL files are append-only and
resumable; `latest.jsonl` and summary tables are deterministic materializations
of the newest row for every task key.

The final report command refuses to claim completion unless all five methods
have 832 rows, all rows use `gpt-5-nano` and the same protocol hash, every
model-facing target is hidden, and every row has both common-judge and frozen
sample bindings. It writes `final_report.md`, `final_results.csv`,
`failures.csv`, and `completion_audit.json`.

## Timing and AutoSpec runtime guards

`generation_seconds` is measured with `time.perf_counter()` around one complete
task invocation. It is wall-clock time and includes the model request, native
tool processing, Frama-C calls, and internal retries. The summary adds those
per-task durations, so under multiple workers it is accumulated task-seconds,
not the end-to-end batch makespan. Imported legacy runs retain batch wall time
separately because per-task durations cannot be recovered faithfully.
`summary.csv` therefore also reports `generation_time_accounting`,
`generation_task_seconds`, `generation_task_rows`, and
`generation_task_mean_seconds`; legacy batch wall time is never presented as a
per-task measurement.

Fresh AutoSpec runs use three independent runtime guards:

- the runner's `--timeout 600` is the maximum wall time for one benchmark;
- `AUTOSPEC_FRAMAC_WALL_TIMEOUT=60` bounds one Frama-C subprocess;
- `AUTOSPEC_FRAMAC_MAX_ATTEMPTS=16` bounds the internal retry loop, which also
  stops immediately when the target file and candidate set do not change.

Both the native wrapper and AutoSpec's Frama-C wrapper use dedicated process
groups and terminate the whole group on timeout or interruption. This prevents
`conda`, Python, Frama-C, Alt-Ergo, or Z3 descendants from surviving a timed-out
task. The external-source changes are preserved in
`autospec_runtime_guard.patch`; apply it from the AutoSpec repository root with
`patch -p1 < /home/yangfp/loopGym/experiments/gpt5nano_full832/autospec_runtime_guard.patch`.
