#!/usr/bin/env python3
"""Generate new training programs for evaluation structural cells the pool lacks.

Input: ``paper/artifacts/v4/eval_cell_specs.json`` (per evaluation cell: how
many evaluation programs it holds, how many training programs the pool has,
and a *structural spec* -- guard kind, variable counts, branch counts,
nondeterminism, update kinds).  For every cell whose supply is below its
demand, an OpenAI-compatible model is asked for batches of fresh single-loop
C functions that satisfy the spec.  Only the spec is sent: no evaluation
program text ever reaches the prompt; few-shot examples come from the
training pool.

Accepted programs must parse as a single-loop program, land in a cell that is
under-supplied, and be no near-copy of any evaluation program at ANY
fingerprint level (exact / alpha / alpha-const / loop-only alpha-const) nor
of an already accepted generated program.  Output is a JSON list of
``{"source", "cell", "spec"}`` plus a report; downstream the sources are
appended to the RL pool (schema rows) and to the SFT program list.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper.scripts._curation_common import digest_of  # noqa: E402
from paper.scripts.filter_training_by_negative_coverage import (  # noqa: E402
    _atomic_json,
    _display_path,
    _source_from_rl,
)
from paper.scripts.program_fingerprint import (  # noqa: E402
    DUPLICATE_LEVELS,
    EvaluationIndex,
    canonicalize_break_idiom,
    fingerprint,
)
from rl_pipeline.common.program import parse_program, strip_postcondition  # noqa: E402

SYSTEM_PROMPT = """You write small C benchmark programs for loop-invariant inference research.
Rules for every program:
- exactly ONE `while` loop, no `for`/`do`, no nested loops, no function calls except `unknown()`;
- only `int` (or `unsigned int`) scalar variables: no arrays, pointers, structs, floats, strings;
- declare `int unknown();` at the top when nondeterminism is used; `unknown()` returns an arbitrary int;
- no `assert`, no `ensures`, no printf, no comments except an optional `/*@ requires ...; */` precondition before the function;
- a unique function name per program, e.g. `gen_<random5digits>`;
- loops must be able to terminate for the sampled inputs (or exit through `unknown()`), and must do real work on the variables;
- programs must be DIFFERENT from each other in their update rules and constants, not just renamed.
Output ONLY the C programs, each wrapped in ```c ... ``` fences, nothing else."""


def _spec_text(spec: dict) -> str:
    lines = []
    if spec["nondet"] and spec["guard_kind"] == "constant":
        lines.append("- loop guard: `while (unknown())` (the loop exits nondeterministically)")
    elif spec["guard_kind"] == "constant":
        lines.append("- loop guard: `while (1)` that exits through an `if (...) break;` inside the body")
    elif spec["guard_kind"] == "single_var":
        lines.append("- loop guard: one variable compared with a constant, e.g. `while (i < 100)`")
    elif spec["guard_kind"] == "var_vs_var":
        lines.append("- loop guard: a comparison between two variables, e.g. `while (i < n)`")
    else:
        lines.append("- loop guard: a compound condition with `&&` or `||` over variables")
    lines.append(f"- about {spec['n_pre_vars']}{'+' if spec['n_pre_vars'] >= 6 else ''} integer variables are live at the loop head "
                 f"(function parameters and locals initialized before the loop)")
    lines.append(f"- the loop body assigns {spec['n_modified']} of them")
    if spec["n_if"]:
        lines.append(f"- the body contains {spec['n_if']} `if` statement(s)" + (f" with {spec['n_else']} `else` branch(es)" if spec["n_else"] else ""))
    else:
        lines.append("- the body is straight-line code with no `if`")
    if spec["nondet"]:
        lines.append("- nondeterministic choices `if (unknown())` appear in the body or the guard")
    else:
        lines.append("- no nondeterminism inside the body")
    if spec["compound_conditions"]:
        lines.append("- at least one condition combines two comparisons with `&&` or `||`")
    if spec.get("break"):
        lines.append("- the body contains a `break`")
    kinds = set(spec.get("update_kinds", []))
    if "product" in kinds or spec["nonlinear"]:
        lines.append("- at least one update multiplies two variables (nonlinear, e.g. `y = y * x`)")
    if "linear_mix" in kinds:
        lines.append("- at least one update combines two variables linearly (e.g. `s = s + i`, `x = x + 2*y`)")
    if "increment" in kinds and "linear_mix" not in kinds and "product" not in kinds:
        lines.append("- updates are increments/decrements by constants")
    if spec.get("div_mod"):
        lines.append("- the body uses `/` or `%`")
    if spec.get("requires"):
        lines.append("- start with a `/*@ requires ...; */` precondition relating the parameters")
    return "\n".join(lines)


def _extract_programs(text: str) -> List[str]:
    blocks = re.findall(r"```(?:c|C)?\s*\n(.*?)```", text, flags=re.DOTALL)
    return [b.strip() + "\n" for b in blocks if "while" in b]


class Acceptor:
    def __init__(self, index: EvaluationIndex, wanted_cells: Dict[str, int],
                 previous: Optional[List[dict]] = None):
        self.index = index
        self.wanted = dict(wanted_cells)
        self.seen_levels: Dict[str, set] = {level: set() for level in DUPLICATE_LEVELS}
        self.lock = threading.Lock()
        self.reasons: Counter = Counter()
        self.accepted: List[dict] = []
        for item in previous or []:  # earlier output seeds de-duplication
            keys = fingerprint(item["source"]).level_keys()
            for level in DUPLICATE_LEVELS:
                self.seen_levels[level].add(keys[level])

    def offer(self, source: str, target_cell: str) -> Optional[str]:
        reason = self._verdict(source, target_cell)
        with self.lock:
            self.reasons[reason or "accepted"] += 1
        return reason

    def _verdict(self, source: str, target_cell: str) -> Optional[str]:
        source, _ = canonicalize_break_idiom(source)
        try:
            program = parse_program(strip_postcondition(source))
        except Exception:
            return "unparsable"
        if len(program.loops) != 1:
            return "loop_count"
        # arrays, pointer declarations, non-integer types
        if re.search(r"\[|\b(?:int|long|short|unsigned|signed)\s*\*\s*[A-Za-z_]|\bfloat\b|\bdouble\b|\bchar\b", source):
            return "unsupported_type"
        verdict = self.index.assess(source, neighbours=0, dedup_levels=DUPLICATE_LEVELS)
        if verdict["copy_levels"]:
            return "eval_copy_" + verdict["copy_levels"][0]
        fp = verdict["fingerprint"]
        with self.lock:
            for level in DUPLICATE_LEVELS:
                if fp[level] in self.seen_levels[level]:
                    return "self_copy_" + level
            cell = verdict["cell"]
            if self.wanted.get(cell, 0) <= 0:
                return "cell_not_wanted" if cell != target_cell else "cell_full"
            self.wanted[cell] -= 1
            for level in DUPLICATE_LEVELS:
                self.seen_levels[level].add(fp[level])
            self.accepted.append({"source": source, "cell": cell, "target_cell": target_cell,
                                  "on_target": cell == target_cell})
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--specs", type=Path, default=ROOT / "paper/artifacts/v4/eval_cell_specs.json")
    parser.add_argument("--pool", type=Path, default=ROOT / "traindata/craft_rl_canonical.parquet",
                        help="training pool for few-shot style examples")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--per-call", type=int, default=8, help="programs requested per API call")
    parser.add_argument("--max-per-cell", type=int, default=120)
    parser.add_argument("--overshoot", type=float, default=1.6, help="request this multiple of the gap")
    parser.add_argument("--api-workers", type=int, default=24)
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument("--limit-cells", type=int)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--existing", type=Path, action="append", default=[],
                        help="previously generated JSON: seeds de-duplication and reduces per-cell demand")
    args = parser.parse_args()

    from experiments.gpt5nano_full832.api import RecordingChat
    import pyarrow.parquet as pq

    specs = json.loads(args.specs.read_text(encoding="utf-8"))
    previous = [item for path in args.existing for item in json.loads(path.read_text(encoding="utf-8"))]
    previous_cells = Counter(item["cell"] for item in previous)
    gaps = []
    for row in specs:
        gap = min(args.max_per_cell, max(0, row["demand_at_6000"] - row["supply_full_pool"] - previous_cells.get(row["cell"], 0)))
        if gap > 0:
            gaps.append((row["cell"], gap, row["spec"]))
    gaps.sort(key=lambda g: -g[1])
    if args.limit_cells:
        gaps = gaps[: args.limit_cells]
    wanted = {cell: gap for cell, gap, _ in gaps}
    print(f"cells to fill: {len(gaps)}, programs wanted: {sum(wanted.values())}", flush=True)

    rng = random.Random(args.seed)
    pool = [_source_from_rl(r).strip()
            for r in pq.read_table(args.pool, columns=["prompt"]).to_pylist()]
    pool = [p for p in pool if 120 < len(p) < 600]
    index = EvaluationIndex.from_evaluation_dirs()
    acceptor = Acceptor(index, wanted, previous=previous)
    chat = RecordingChat(model=args.model, system_prompt=SYSTEM_PROMPT, use_default_system_prompt=False,
                         reasoning_effort="omit", max_completion_tokens=args.max_tokens)

    jobs = []
    for cell, gap, spec in gaps:
        calls = max(1, round(gap * args.overshoot / args.per_call))
        for k in range(calls):
            examples = rng.sample(pool, 2)
            jobs.append((cell, spec, examples, k))

    def run(job):
        cell, spec, examples, k = job
        prompt = (
            f"Write {args.per_call} distinct C programs that ALL satisfy this structural specification:\n"
            f"{_spec_text(spec)}\n\n"
            "Two unrelated example programs from our corpus, for style only (do not copy their logic):\n"
            f"```c\n{examples[0]}\n```\n```c\n{examples[1]}\n```\n"
            f"Vary constants, variable names, update rules and branch conditions across the {args.per_call} programs."
        )
        try:
            text = chat.chat(prompt)
        except Exception as error:
            return cell, [], f"{type(error).__name__}: {str(error)[:120]}"
        return cell, _extract_programs(text), None

    errors = Counter()
    with ThreadPoolExecutor(max_workers=args.api_workers) as executor:
        futures = [executor.submit(run, job) for job in jobs]
        for count, future in enumerate(as_completed(futures), 1):
            cell, programs, error = future.result()
            if error:
                errors[error[:40]] += 1
            for source in programs:
                acceptor.offer(source, cell)
            if count % 20 == 0 or count == len(futures):
                print(f"[generate] {count}/{len(futures)} accepted={len(acceptor.accepted)} "
                      f"reasons={dict(acceptor.reasons.most_common(6))} usage={chat.usage()}", flush=True)

    accepted = acceptor.accepted
    for item in accepted:
        item["digest"] = digest_of(item["source"])
    _atomic_json(accepted, args.output)
    filled = Counter(item["cell"] for item in accepted)
    report = {
        "schema_version": 1,
        "specs": _display_path(args.specs),
        "output": _display_path(args.output),
        "model": chat.model,
        "cells_targeted": len(gaps),
        "programs_wanted": sum(wanted.values()),
        "programs_accepted": len(accepted),
        "on_target_cell": sum(1 for item in accepted if item["on_target"]),
        "cells_filled": len(filled),
        "eval_programs_newly_covered": sum(index.cell_counts[c] for c in filled if c in index.cell_counts),
        "rejections": dict(acceptor.reasons),
        "api_errors": dict(errors),
        "usage": chat.usage(),
        "policy": "only the structural spec is prompted; every evaluation-program fingerprint level "
                  "(exact/alpha/alpha_const/alpha_const_loop) is a rejection; generated programs are "
                  "also de-duplicated against each other at every level",
    }
    _atomic_json(report, args.report)
    print(json.dumps({k: report[k] for k in ("programs_wanted", "programs_accepted", "on_target_cell",
                                              "cells_filled", "eval_programs_newly_covered", "rejections")}, indent=2))


if __name__ == "__main__":
    main()
