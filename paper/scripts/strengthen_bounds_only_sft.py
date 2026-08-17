#!/usr/bin/env python3
"""Strengthen every bounds-only answer in the released SFT set.

Candidates are generated per program and accepted only when the existing
Frama-C/WP Houdini pipeline retains them together with the original answer.
The script writes an itemized audit so no row is strengthened merely by a
syntactic non-bound formula.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from paper.scripts._curation_common import record_source_and_answer  # noqa: E402
from paper.scripts.sanitize_training_prompts import (  # noqa: E402
    _constant_integer_bound,
    _loop_entry_resolver,
    _rejection_reason,
    _synchronous_linear_relation,
)
from rl_pipeline.common.program import Program, parse_program  # noqa: E402
from rl_pipeline.common.state import (  # noqa: E402
    dedup_normalized,
    extract_invariants,
    normalize_invariant,
)
from rl_pipeline.reward.filters import HoudiniFilter  # noqa: E402
from rl_pipeline.sampler.example_sampler import ExampleSampler  # noqa: E402


DEFAULT_SFT = ROOT / "traindata" / "craft_sft_clean.json"
DEFAULT_AUDIT = ROOT / "paper" / "artifacts" / "sft_bounds_strengthening.json"
_DIRECT_UNKNOWN = re.compile(r"^\s*unknown\w*\s*\(\s*\)\s*$")
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
_INTEGER = r"(?:0[xX][0-9A-Fa-f]+|\d+)"


def _is_bounds_only(clauses: list[str]) -> bool:
    return bool(clauses) and all(
        _constant_integer_bound(normalize_invariant(clause)) is not None
        for clause in clauses
    )


def _integer(text: str) -> int | None:
    text = text.strip()
    try:
        return int(text, 0)
    except ValueError:
        return None


def _assigned(program: Program) -> set[str]:
    return ExampleSampler._assigned_names(program.loop.body)


def _frame_candidates(program: Program) -> list[tuple[str, str]]:
    """Entry equalities for loop-relevant variables not written by the loop."""
    assigned = _assigned(program)
    relevant = set(_IDENTIFIER.findall(program.loop.guard + " " + program.loop.body))
    resolve = _loop_entry_resolver(program)
    candidates: list[tuple[str, str]] = []
    for variable in program.pre_vars:
        if variable in assigned or variable not in relevant:
            continue
        entry, _needs_entry = resolve(variable)
        if entry is None or normalize_invariant(entry) == variable:
            continue
        candidates.append((f"{variable} == {entry}", "loop-relevant frame relation"))
    return candidates


def _monotone_candidates(program: Program) -> list[tuple[str, str]]:
    """Tight endpoint and phase facts for simple threshold-step recurrences."""
    guard = normalize_invariant(program.loop.guard)
    body = program.loop.body
    candidates: list[tuple[str, str]] = []
    upper = re.fullmatch(rf"([A-Za-z_]\w*)\s*(<|<=)\s*({_INTEGER})", guard)
    lower = re.fullmatch(rf"([A-Za-z_]\w*)\s*(>|>=)\s*({_INTEGER})", guard)

    def deltas(variable: str) -> list[int]:
        escaped = re.escape(variable)
        values: list[int] = []
        values += [1] * len(re.findall(rf"\b{escaped}\s*\+\+|\+\+\s*\b{escaped}\b", body))
        values += [-1] * len(re.findall(rf"\b{escaped}\s*--|--\s*\b{escaped}\b", body))
        for sign, raw in re.findall(rf"\b{escaped}\s*([+-])=\s*({_INTEGER})", body):
            value = int(raw, 0)
            values.append(value if sign == "+" else -value)
        for sign, raw in re.findall(
            rf"\b{escaped}\s*=\s*{escaped}\s*([+-])\s*({_INTEGER})", body
        ):
            value = int(raw, 0)
            values.append(value if sign == "+" else -value)
        return values

    if upper:
        variable, operator, raw_limit = upper.groups()
        limit = int(raw_limit, 0)
        ds = [delta for delta in deltas(variable) if delta > 0]
        if ds:
            last_enabled = limit if operator == "<=" else limit - 1
            candidates.append(
                (f"{variable} <= {last_enabled + max(ds)}", "tight guard-overshoot bound")
            )
    if lower:
        variable, operator, raw_limit = lower.groups()
        limit = int(raw_limit, 0)
        ds = [delta for delta in deltas(variable) if delta < 0]
        if ds:
            last_enabled = limit if operator == ">=" else limit + 1
            candidates.append(
                (f"{variable} >= {last_enabled + min(ds)}", "tight guard-overshoot bound")
            )

    # A threshold followed by a fixed step creates a genuine modular phase.
    branch = re.search(
        rf"if\s*\(\s*([A-Za-z_]\w*)\s*<\s*({_INTEGER})\s*\)\s*\{{(.*?)\}}\s*else\s*\{{(.*?)\}}",
        body,
        flags=re.DOTALL,
    )
    if branch:
        variable, raw_threshold, _first, second = branch.groups()
        threshold = int(raw_threshold, 0)
        escaped = re.escape(variable)
        match = re.search(rf"\b{escaped}\s*\+=\s*({_INTEGER})", second)
        if match and int(match.group(1), 0) > 1:
            step = int(match.group(1), 0)
            candidates.append(
                (
                    f"{variable} < {threshold} || {variable} % {step} == {threshold % step}",
                    "post-threshold modular phase",
                )
            )
    return candidates


def _parity_candidates(program: Program) -> list[tuple[str, str]]:
    """Relations for a stable selector choosing a fixed counter step."""
    body = program.loop.body
    candidates: list[tuple[str, str]] = []
    inits = dict(program.local_inits)
    for selector, modulus, residue, block in re.findall(
        rf"if\s*\(\s*([A-Za-z_]\w*)\s*%\s*({_INTEGER})\s*==\s*({_INTEGER})\s*\)\s*\{{(.*?)\}}",
        body,
        flags=re.DOTALL,
    ):
        if selector in _assigned(program):
            continue
        mod = int(modulus, 0)
        rem = int(residue, 0)
        for variable, raw_step in re.findall(
            rf"\b([A-Za-z_]\w*)\s*\+=\s*({_INTEGER})", block
        ):
            step = int(raw_step, 0)
            initial = _integer(inits.get(variable, ""))
            if step > 1 and initial is not None:
                candidates.append(
                    (
                        f"{selector} % {mod} == {rem} ==> {variable} % {step} == {initial % step}",
                        "selector-conditioned modular phase",
                    )
                )
    # The complementary arm can be the fixed-step arm.
    for selector, modulus, residue, block in re.findall(
        rf"if\s*\(\s*([A-Za-z_]\w*)\s*%\s*({_INTEGER})\s*==\s*({_INTEGER})\s*\)\s*"
        rf"\{{.*?\}}\s*else\s*\{{(.*?)\}}",
        body,
        flags=re.DOTALL,
    ):
        if selector in _assigned(program):
            continue
        mod = int(modulus, 0)
        rem = int(residue, 0)
        for variable, raw_step in re.findall(
            rf"\b([A-Za-z_]\w*)\s*\+=\s*({_INTEGER})", block
        ):
            step = int(raw_step, 0)
            initial = _integer(inits.get(variable, ""))
            if step > 1 and initial is not None:
                candidates.append(
                    (
                        f"{selector} % {mod} != {rem} ==> {variable} % {step} == {initial % step}",
                        "selector-conditioned modular phase",
                    )
                )
    return candidates


def _assignment_relations(program: Program) -> list[tuple[str, str]]:
    """Hand-auditable relational laws for common benchmark recurrences."""
    body = program.loop.body
    candidates: list[tuple[str, str]] = []
    synchronous = _synchronous_linear_relation(
        program, [f"{variable} >= -2147483648" for variable in program.pre_vars]
    )
    if synchronous is not None:
        candidates.append((synchronous, "synchronous constant-update conservation law"))

    # y = n - x followed by x = x + 1.  The first disjunct covers initialization.
    for y, n, x in re.findall(
        r"\b([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*-\s*([A-Za-z_]\w*)\s*;",
        body,
    ):
        if re.search(rf"\b{re.escape(x)}\s*=\s*{re.escape(x)}\s*\+\s*1\s*;", body):
            initial = _integer(dict(program.local_inits).get(x, ""))
            if initial is not None:
                candidates.append(
                    (f"{x} == {initial} || {x} + {y} == {n} + 1", "assignment/update phase relation")
                )

    # Russian-peasant multiplication family: a*b*p+q is conserved.
    scale = re.search(
        r"\b([A-Za-z_]\w*)\s*=\s*4\s*\*\s*\1\s*;", body
    )
    inits = dict(program.local_inits)
    if scale:
        factor = scale.group(1)
        operands = [name for name, init in program.local_inits if init in program.params]
        accumulators = [name for name, init in program.local_inits if _integer(init) == 0]
        if len(operands) >= 2 and accumulators:
            left, right = operands[:2]
            acc = accumulators[-1]
            left_pre = inits[left]
            right_pre = inits[right]
            candidates.append(
                (
                    f"{left} * {right} * {factor} + {acc} == "
                    f"\\at({left_pre},Pre) * \\at({right_pre},Pre)",
                    "multiplicative conservation law",
                )
            )

    # A value initialized to one and only multiplied by a fixed factor stays
    # either at one or in the corresponding modular phase.
    for variable, raw_factor in re.findall(
        rf"\b([A-Za-z_]\w*)\s*=\s*({_INTEGER})\s*\*\s*\1\s*;", body
    ):
        factor = int(raw_factor, 0)
        if factor > 1 and _integer(inits.get(variable, "")) == 1:
            candidates.append(
                (
                    f"{variable} == 1 || {variable} % {factor} == 0",
                    "fixed-multiplier modular phase",
                )
            )

    # Binary decomposition: doubling the multiplicand on an even step and
    # moving it into the accumulator on an odd step conserves e*j+q.
    source = normalize_invariant(program.source)
    binary_shapes = [
        ("e", "j", "q", "v8", "h"),
        ("gh", "ef", "g", "u", "v4"),
    ]
    for multiplicand, multiplier, accumulator, left, right in binary_shapes:
        required = {multiplicand, multiplier, accumulator, left, right}
        if required.issubset(set(program.pre_vars)) and re.search(
            rf"\b{re.escape(multiplicand)}\s*=\s*\(?2\s*\*\s*{re.escape(multiplicand)}\)?",
            body,
        ):
            candidates.append(
                (
                    f"{multiplicand} * {multiplier} + {accumulator} == {left} * {right}",
                    "binary-decomposition conservation law",
                )
            )

    # Two coupled subtractive recurrences maintain Bézout representations.
    if {"u", "v2", "w", "v4", "v1", "d", "f", "ctr"}.issubset(
        set(program.pre_vars)
    ) and "w = w - v4" in body and "d = d - v1" in body:
        candidates.extend(
            [
                ("u == w * f + v1 * ctr", "Bézout conservation law for u"),
                ("v2 == v4 * f + d * ctr", "Bézout conservation law for v2"),
            ]
        )

    # Nested-loop benchmark: both accumulators take the same base update and
    # cur may receive one additional positive update.
    if {"cur", "b"}.issubset(set(program.pre_vars)) and "cur+=3" in body and "b+=3" in body:
        candidates.append(("cur >= b", "coupled-accumulator order relation"))
    if {"k", "b"}.issubset(set(program.pre_vars)) and "k+=3" in body and "b+=3" in body:
        candidates.append(("k >= b", "coupled-accumulator order relation"))

    # A current parameter that moves monotonically away from its Pre value.
    for variable in program.params:
        escaped = re.escape(variable)
        increases = bool(
            re.search(rf"\b{escaped}\s*(?:\+\+|\+=\s*[1-9]|=\s*[^;]*\+\s*[1-9])", body)
        )
        decreases = bool(
            re.search(rf"\b{escaped}\s*(?:--|-=\s*[1-9]|=\s*[^;]*-\s*[1-9])", body)
        )
        if increases and not decreases:
            candidates.append(
                (f"{variable} >= \\at({variable},Pre)", "monotonic progress from function entry")
            )
        if decreases and not increases:
            candidates.append(
                (f"{variable} <= \\at({variable},Pre)", "monotonic progress from function entry")
            )

    # An unknown local and a deterministic counter updated in lockstep.
    unknowns = [name for name, init in program.local_inits if _DIRECT_UNKNOWN.fullmatch(init)]
    counters = [name for name, init in program.local_inits if _integer(init) is not None]
    for unknown in unknowns:
        for counter in counters:
            u_add = re.search(rf"\b{re.escape(unknown)}\s*=\s*{re.escape(unknown)}\s*\+\s*({_INTEGER})", body)
            c_inc = re.search(rf"\b{re.escape(counter)}\s*\+\+", body)
            if u_add and c_inc:
                step = int(u_add.group(1), 0)
                initial = int(inits[counter], 0)
                candidates.append(
                    (
                        f"{unknown} == \\at({unknown},LoopEntry) + {step} * ({counter} - {initial})",
                        "unknown-initialized lockstep conservation law",
                    )
                )
    return candidates


def candidates_for(program: Program) -> list[tuple[str, str]]:
    raw = (
        _assignment_relations(program)
        + _parity_candidates(program)
        + _monotone_candidates(program)
        + _frame_candidates(program)
    )
    accepted: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate, reason in raw:
        candidate = normalize_invariant(candidate)
        if candidate in seen or _rejection_reason(candidate, program) is not None:
            continue
        seen.add(candidate)
        accepted.append((candidate, reason))
    return accepted


def _verify(job: tuple[int, str, list[str], list[tuple[str, str]]]):
    index, source, original, candidates = job
    program = parse_program(source)
    pool = dedup_normalized(original + [candidate for candidate, _ in candidates])
    survivors = dedup_normalized(HoudiniFilter().filter(program, 0, pool, None))
    original_keys = set(dedup_normalized(original))
    additions = [candidate for candidate in survivors if candidate not in original_keys]
    reasons = {candidate: reason for candidate, reason in candidates}
    return index, additions, {candidate: reasons[candidate] for candidate in additions}, survivors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sft", type=Path, default=DEFAULT_SFT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--jobs", type=int, default=min(12, os.cpu_count() or 1))
    parser.add_argument("--wp-timeout", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    records = json.loads(args.sft.read_text(encoding="utf-8"))
    jobs = []
    audits: dict[int, dict] = {}
    for index, record in enumerate(records):
        source, answer = record_source_and_answer(record)
        original = extract_invariants(answer)
        if not _is_bounds_only(original):
            continue
        program = parse_program(source)
        candidates = candidates_for(program)
        jobs.append((index, source, original, candidates))
        audits[index] = {
            "row": index,
            "function": program.func_name,
            "original": original,
            "candidates": [
                {"clause": candidate, "rationale": reason}
                for candidate, reason in candidates
            ],
        }
    os.environ["CRAFT_WP_TIMEOUT"] = str(args.wp_timeout)
    results = []
    with ProcessPoolExecutor(max_workers=args.jobs) as executor:
        futures = [executor.submit(_verify, job) for job in jobs]
        for completed, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            if completed % 10 == 0 or completed == len(futures):
                print(f"WP checked {completed}/{len(futures)} bounds-only answers", flush=True)
    strengthened = copy.deepcopy(records)
    for index, additions, reasons, survivors in results:
        audits[index]["wp_additions"] = additions
        audits[index]["addition_rationales"] = reasons
        audits[index]["strengthened"] = bool(additions)
        if additions:
            answer = "\n".join(f"loop invariant {clause};" for clause in survivors)
            turn = next(
                turn for turn in strengthened[index]["conversations"] if turn["from"] == "gpt"
            )
            turn["value"] = answer
    remaining = sorted(index for index, audit in audits.items() if not audit["strengthened"])
    report = {
        "schema_version": 1,
        "source": str(args.sft),
        "bounds_only_before": len(jobs),
        "strengthened": len(jobs) - len(remaining),
        "remaining": remaining,
        "items": [audits[index] for index in sorted(audits)],
    }
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.output and not args.dry_run:
        args.output.write_text(json.dumps(strengthened, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("bounds_only_before", "strengthened", "remaining")}, indent=2))


if __name__ == "__main__":
    main()
