#!/usr/bin/env python3
"""One-task runners for target-hidden LORIS and LaM4Inv adaptations.

The model-facing source never contains the assertion/postcondition.  The
restored target is deliberately left to the CRAFT common judge.  This module
is invoked in a subprocess by :mod:`near_neighbor_adapter`, which makes the
upstream packages' global logging and solver state safe under parallel runs.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

from openai import OpenAI


LORIS_ROOT = Path(os.environ.get("LORIS_ROOT", "/home/yangfp/LORIS"))
CLAUSE2INV_ROOT = Path(
    os.environ.get("CLAUSE2INV_ROOT", "/home/yangfp/Clause2Inv")
)


class RecordedLLM:
    """LORIS-compatible chat client with exact provider usage records."""

    def __init__(self, model: str):
        self.model = model
        self.client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
        self.records: list[dict[str, Any]] = []

    def get_response_by_prompt(
        self,
        prompt: str,
        chat_history: list[dict] | None = None,
        input_token_list: list[int] | None = None,
        output_token_list: list[int] | None = None,
    ) -> tuple[str, list[dict]]:
        messages = list(chat_history or []) + [{"role": "user", "content": prompt}]
        started = time.perf_counter()
        response = None
        for retry in range(6):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_completion_tokens=8192,
                    reasoning_effort="none",
                )
                break
            except Exception:
                if retry == 5:
                    raise
                time.sleep(min(2 ** retry, 15))
        assert response is not None
        text = response.choices[0].message.content or ""
        usage = response.usage
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(
            getattr(usage, "total_tokens", prompt_tokens + completion_tokens)
            or prompt_tokens + completion_tokens
        )
        if input_token_list is not None:
            input_token_list.append(prompt_tokens)
        if output_token_list is not None:
            output_token_list.append(completion_tokens)
        self.records.append({
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "seconds": time.perf_counter() - started,
            "finish_reason": response.choices[0].finish_reason,
            "response": text,
        })
        return text, messages + [{"role": "assistant", "content": text}]

    def parse_session(self, chat_history: list[dict]) -> str:
        return "\n".join(
            f"-----------Role: {item['role']}-----------\n{item['content']}"
            for item in chat_history
        )


def _usage(records: list[dict]) -> dict:
    return {
        "prompt_tokens": sum(row["prompt_tokens"] for row in records),
        "completion_tokens": sum(row["completion_tokens"] for row in records),
        "total_tokens": sum(row["total_tokens"] for row in records),
        "api_call_count": len(records),
        "token_accounting": "exact" if records else "not_called",
    }


def _extract_assertions(text: str) -> list[str]:
    """Extract balanced ``assert(...)`` expressions from a model response."""
    invariants: list[str] = []
    for match in re.finditer(r"\bassert\s*\(", text):
        depth = 1
        start = match.end()
        quote = None
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if quote:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == quote:
                    quote = None
                continue
            if char in {'"', "'"}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    value = text[start:index].strip()
                    if value and value not in invariants:
                        invariants.append(value)
                    break
    return invariants


def _extract_loris_block(block: str) -> list[str]:
    values = []
    for value in re.findall(r"loop invariant(?:\s+\w+:)?\s*(.*?);", block):
        value = value.strip()
        if value and value not in values:
            values.append(value)
    return values


def run_loris(args: argparse.Namespace) -> dict:
    sys.path.insert(0, str(LORIS_ROOT / "src"))
    import frama_c
    from StepProofVerifier.Config import Config
    from StepProofVerifier.Prompt import Prompt
    from StepProofVerifier.StepProofVerifier import StepProofVerifier

    # LORIS's released command hard-codes CVC4.  The common Frama-C 27.1
    # environment provides Alt-Ergo and Z3 but not CVC4; Frama-C otherwise
    # aborts before producing the CSV report that LORIS parses.  Keep the
    # upstream checker and proof-status parser, changing only that unavailable
    # prover entry at process launch.
    native_popen = frama_c.subprocess.Popen

    def available_provers_popen(command, *popen_args, **popen_kwargs):
        if isinstance(command, list):
            command = [
                item.replace("-wp-prover=alt-ergo,z3,cvc4", "-wp-prover=alt-ergo,z3")
                for item in command
            ]
        return native_popen(command, *popen_args, **popen_kwargs)

    frama_c.subprocess.Popen = available_provers_popen

    # The upstream wording asks candidates to prove the visible assertion.
    # Under the common target-hidden protocol we retain LORIS's format and
    # local establishment/preservation repair, but remove that target cue.
    def hidden_initial_prompt(code: str) -> str:
        return f"""{code}

Print useful inductive loop invariants as valid C assertions.  Each invariant
must hold before the loop and after every iteration.  The verification target
is intentionally unavailable, so infer general loop-state relationships from
the preconditions, guard, and body only.  Do not explain.  Use this format:

```c
assert(...);
assert(...);
```
"""

    Prompt.get_init_invariants_prompt = staticmethod(hidden_initial_prompt)
    config = Config()
    config.model = args.model
    config.timeout = args.timeout
    config.input_limit = 100_000
    config.output_limit = 50_000
    config.token_limit = 150_000
    config.no_preprocess = True
    config.BMC = False
    verifier = StepProofVerifier(str(args.source), config)
    native_get_labels = verifier.benchmark.get_labels

    def non_acsl_template_labels(code: str):
        # In no-preprocess mode the released frontend treats every block
        # comment as an insertion label.  Benchmark `/*@ requires ... */`
        # contracts are ACSL, not LORIS template labels; regex metacharacters
        # in such contracts can otherwise make re.findall return tuples.
        return [
            label for label in native_get_labels(code)
            if not label[1].lstrip().startswith("@")
        ]

    verifier.benchmark.get_labels = non_acsl_template_labels
    recorded = RecordedLLM(args.model)
    verifier.llm = recorded
    verifier.max_iter = 10
    started = time.perf_counter()
    success, block, iterations, _input, _output, _bmc = verifier.runWithShortenTokens(
        log_path=str(args.log)
    )
    return {
        "native_candidate_found": bool(success),
        "native_verified": bool(success),
        "native_iterations": iterations,
        "invariants": _extract_loris_block(block),
        "raw_responses": [row["response"] for row in recorded.records],
        "api_records": recorded.records,
        "generation_seconds": time.perf_counter() - started,
        **_usage(recorded.records),
    }


LAM_INITIAL = """{program}

Print loop invariants as valid C assertions.  The verification target is
hidden: use only the preconditions, loop guard, and loop body.  Consider both
zero iterations and loop exit.  Use && or || if necessary.  Do not explain.
Return one or more lines of the form `assert(...);`.
"""

LAM_REPAIR = """{program}

The verification target is hidden.  Your previous candidate was:
{previous}

It failed the target-independent {kind} check with this counterexample:
{counterexample}

Propose corrected inductive loop invariants using only the preconditions,
guard, body, and this counterexample.  Do not explain; return `assert(...);`.
"""


def _lam_vc(args: argparse.Namespace) -> Path:
    if args.suite == "linear":
        return CLAUSE2INV_ROOT / "combinator/Benchmarks/Linear/c_smt2" / (
            args.case_id + ".c.smt"
        )
    if args.suite == "NLA_lipus":
        return CLAUSE2INV_ROOT / "combinator/Benchmarks/NL/c_smt" / (
            "NL" + args.case_id + ".c_smt"
        )
    raise ValueError("LaM4Inv has no precomputed VCs for this suite")


def run_lam4inv(args: argparse.Namespace) -> dict:
    sys.path.insert(0, str(CLAUSE2INV_ROOT / "combinator"))
    from c_inv_checker import inv_solver

    vc = _lam_vc(args)
    program = args.source.read_text(errors="ignore")
    llm = RecordedLLM(args.model)
    candidates: list[str] = []
    responses: list[str] = []
    started = time.perf_counter()

    # LaM4Inv's released implementation begins with five proposals and then
    # filters/reassembles their predicates.  In the hidden adaptation, only
    # the target-free initiation and transition VCs are queried.
    for _ in range(5):
        response, _history = llm.get_response_by_prompt(LAM_INITIAL.format(program=program))
        responses.append(response)
        for candidate in _extract_assertions(response):
            if candidate not in candidates:
                candidates.append(candidate)

    accepted: list[str] = []
    last_kind = "initiation/preservation"
    last_ce: Any = None
    checks = 0
    cursor = 0
    while time.perf_counter() - started < args.timeout and checks < 50:
        if cursor >= len(candidates):
            previous = " && ".join(f"({item})" for item in accepted) or "true"
            response, _history = llm.get_response_by_prompt(
                LAM_REPAIR.format(
                    program=program,
                    previous=previous,
                    kind=last_kind,
                    counterexample=last_ce,
                )
            )
            responses.append(response)
            new = [item for item in _extract_assertions(response) if item not in candidates]
            if not new:
                break
            candidates.extend(new)

        candidate = candidates[cursor]
        cursor += 1
        checks += 1
        try:
            result = inv_solver(str(vc), candidate, checks=("pre", "loop"))
        except Exception:
            continue
        if result[0] is None and result[1] is None:
            accepted.append(candidate)
        else:
            last_kind = "initiation" if result[0] is not None else "preservation"
            last_ce = result[0] if result[0] is not None else result[1]

        # Reassemble the retained predicates, as in LaM4Inv, and accept once
        # the conjunction is inductive.  No postcondition VC is parsed here.
        if accepted:
            combined = " && ".join(f"({item})" for item in accepted)
            try:
                combined_result = inv_solver(
                    str(vc), combined, checks=("pre", "loop")
                )
            except Exception:
                combined_result = ["EXCEPT", "EXCEPT", None]
            if combined_result[0] is None and combined_result[1] is None:
                break

    return {
        "native_candidate_found": bool(accepted),
        "native_verified": bool(accepted),
        "native_checks": checks,
        "invariants": accepted,
        "raw_responses": responses,
        "api_records": llm.records,
        "generation_seconds": time.perf_counter() - started,
        **_usage(llm.records),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--method", choices=("loris", "lam4inv"), required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--model", default="gpt-5-nano")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    result = run_loris(args) if args.method == "loris" else run_lam4inv(args)
    print("RESULT_JSON:" + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
