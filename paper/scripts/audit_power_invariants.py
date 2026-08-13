#!/usr/bin/env python3
"""Classify archived SFT ``power`` calls before helper-interface removal."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from paper.scripts.sanitize_training_prompts import (  # noqa: E402
    PROGRAM_MARKER,
    _matching_parenthesis,
    _split_call_arguments,
    _strip_outer_parentheses,
)
from rl_pipeline.common.program import parse_program, strip_postcondition  # noqa: E402
from rl_pipeline.common.state import extract_invariants  # noqa: E402


DEFAULT_SFT = ROOT / "traindata" / "loopgym_sft_0803.json"
CALL_START = re.compile(r"\bpower\s*\(")


def power_calls(expression: str):
    for match in CALL_START.finditer(expression):
        opening = expression.find("(", match.start(), match.end())
        closing = _matching_parenthesis(expression, opening)
        if closing is None:
            yield "", "", "malformed"
            continue
        arguments = _split_call_arguments(expression[opening + 1:closing])
        if arguments is None:
            yield "", "", "malformed"
            continue
        base, exponent = arguments
        exponent = _strip_outer_parentheses(exponent)
        if re.fullmatch(r"\+?\d+", exponent):
            kind = (
                "fixed_expandable"
                if int(exponent) <= 20
                else "fixed_too_large"
            )
        elif re.fullmatch(r"[A-Za-z_]\w*", exponent):
            kind = "symbolic_variable"
        else:
            kind = "symbolic_expression"
        yield base.strip(), exponent.strip(), kind


def small_source_domain(program, variable: str):
    inits = dict(program.local_inits)
    initial_text = inits.get(variable)
    if initial_text is None or not re.fullmatch(r"[+-]?\d+", initial_text.strip()):
        return None
    initial = int(initial_text)
    body = program.loop.body
    increment = bool(
        re.search(rf"\b{re.escape(variable)}\s*\+\+", body)
        or re.search(rf"\+\+\s*{re.escape(variable)}\b", body)
        or re.search(rf"\b{re.escape(variable)}\s*\+=\s*1\b", body)
        or re.search(rf"\b{re.escape(variable)}\s*=\s*{re.escape(variable)}\s*\+\s*1\b", body)
    )
    decrement = bool(
        re.search(rf"\b{re.escape(variable)}\s*--", body)
        or re.search(rf"--\s*{re.escape(variable)}\b", body)
        or re.search(rf"\b{re.escape(variable)}\s*-=\s*1\b", body)
        or re.search(rf"\b{re.escape(variable)}\s*=\s*{re.escape(variable)}\s*-\s*1\b", body)
    )
    guard = _strip_outer_parentheses(program.loop.guard)
    if increment:
        match = re.fullmatch(rf"{re.escape(variable)}\s*(<|<=)\s*([+-]?\d+)", guard)
        if match:
            limit = int(match.group(2)) + int(match.group(1) == "<=")
            domain = list(range(initial, limit + 1))
            return domain if 0 < len(domain) <= 9 else None
    if decrement:
        match = re.fullmatch(rf"{re.escape(variable)}\s*(>|>=)\s*([+-]?\d+)", guard)
        if match:
            limit = int(match.group(2)) - int(match.group(1) == ">=")
            domain = list(range(initial, limit - 1, -1))
            return domain if 0 < len(domain) <= 9 else None
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sft", type=Path, default=DEFAULT_SFT)
    parser.add_argument("--examples", type=int, default=5)
    args = parser.parse_args()

    if not args.sft.is_file():
        parser.error(
            f"archival SFT input not found: {args.sft}; pass it explicitly with --sft"
        )

    records = json.loads(args.sft.read_text(encoding="utf-8"))
    kinds: Counter[str] = Counter()
    exponents: Counter[str] = Counter()
    bases: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    examples: dict[str, list[dict[str, object]]] = defaultdict(list)
    rows_with_power = 0
    clauses_with_power = 0

    for row_index, record in enumerate(records):
        turns = record["conversations"]
        human = next(turn["value"] for turn in turns if turn["from"] == "human")
        answer = next(turn["value"] for turn in turns if turn["from"] == "gpt")
        source = strip_postcondition(human.split(PROGRAM_MARKER, 1)[1])
        try:
            program = parse_program(source)
        except ValueError:
            program = None
        row_hit = False
        for clause in extract_invariants(answer):
            calls = list(power_calls(clause))
            if not calls:
                continue
            row_hit = True
            clauses_with_power += 1
            for base, exponent, kind in calls:
                kinds[kind] += 1
                exponents[exponent] += 1
                bases[base] += 1
                domain = None
                if kind == "symbolic_variable" and program is not None:
                    domain = small_source_domain(program, exponent)
                    domains["small_finite_source_domain" if domain else "no_small_source_domain"] += 1
                if len(examples[kind]) < args.examples:
                    examples[kind].append(
                        {
                            "row": row_index,
                            "base": base,
                            "exponent": exponent,
                            "domain": domain,
                            "guard": program.loop.guard if program else None,
                            "body": program.loop.body.strip() if program else None,
                            "clause": clause,
                        }
                    )
        rows_with_power += int(row_hit)

    report = {
        "rows": len(records),
        "rows_with_power": rows_with_power,
        "clauses_with_power": clauses_with_power,
        "calls_by_kind": dict(kinds.most_common()),
        "symbolic_variable_domains": dict(domains.most_common()),
        "top_exponents": exponents.most_common(30),
        "top_bases": bases.most_common(30),
        "examples": examples,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
