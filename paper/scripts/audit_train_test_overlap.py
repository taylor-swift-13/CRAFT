#!/usr/bin/env python3
"""Reproduce the training--evaluation overlap table in the appendix.

The audit compares target-hidden program text at four increasingly aggressive
normalizations and separately reports three deliberately lossy control-flow
signatures.  It prints one JSON object; it never mutates the datasets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable, Sequence

import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rl_pipeline.common.program import (  # noqa: E402
    iter_acsl_clauses,
    strip_noncontract_comments,
    strip_postcondition,
)


TRAIN_PARQUET = ROOT / "traindata" / "craft_rl_clean.parquet"
SFT_JSON = ROOT / "traindata" / "craft_sft_clean.json"
SYSTEM_PROMPT = ROOT / "prompt" / "system_prompt.txt"
USER_PROMPT = ROOT / "prompt" / "generate_prompt.txt"
EVAL_DIRS = (
    ROOT / "src" / "input" / "linear",
    ROOT / "src" / "input" / "NLA_lipus",
    ROOT / "src" / "input" / "Loopy",
)

TOKEN_RE = re.compile(
    r"==>|<==>|==|!=|<=|>=|&&|\|\||<<|>>|\+\+|--|->|"
    r"[A-Za-z_]\w*|0[xX][0-9A-Fa-f]+|\d+|[^\s]"
)
IDENT_RE = re.compile(r"[A-Za-z_]\w*\Z")
INTEGER_RE = re.compile(r"(?:0[xX][0-9A-Fa-f]+|\d+)\Z")
NONDET_RE = re.compile(r"(?:unknown|nondet)\d*|__VERIFIER_nondet_\w*\Z")
CONDITION_OPS = {"<", ">", "<=", ">=", "==", "!=", "&&", "||", "!"}
CONTROL_WITH_CONDITION = {"if", "while", "for", "switch"}
NONCONTRACT_COMMENT_RE = re.compile(
    r"//(?!@)([^\n]*)|/\*(?!@)(.*?)\*/", re.DOTALL
)
C_KEYWORDS = {
    "_Bool", "auto", "break", "case", "char", "const", "continue",
    "default", "do", "double", "else", "enum", "extern", "float",
    "for", "goto", "if", "inline", "int", "long", "register",
    "requires", "restrict", "return", "short", "signed", "sizeof",
    "static", "struct", "switch", "typedef", "union", "unsigned",
    "void", "volatile", "while",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_whitespace(source: str) -> str:
    return re.sub(r"\s+", " ", source).strip()


def remove_noncontract_comments(source: str) -> str:
    def replace_block(match: re.Match[str]) -> str:
        comment = match.group(0)
        if comment.startswith("/*@") and re.search(r"\brequires\b", comment):
            requirements = re.findall(
                r"\brequires\b\s*(.*?);", comment, flags=re.DOTALL
            )
            return " ".join(f"requires {value};" for value in requirements)
        return " "

    source = re.sub(r"/\*.*?\*/", replace_block, source, flags=re.DOTALL)
    return re.sub(r"//.*", " ", source)


def tokens(source: str) -> list[str]:
    return TOKEN_RE.findall(remove_noncontract_comments(source))


def target_hidden_source(source: str) -> str:
    return normalize_whitespace(strip_postcondition(source))


def near_exact_tokens(source: str) -> str:
    values = tokens(strip_postcondition(source))
    # Only the outer function name is ignored; called functions remain visible.
    for index, value in enumerate(values[:-1]):
        if (
            IDENT_RE.fullmatch(value)
            and values[index + 1] == "("
            and value not in C_KEYWORDS
            and not NONDET_RE.fullmatch(value)
        ):
            values[index] = "FUNC"
            break
    return " ".join(values)


def alpha_normalized(source: str, abstract_constants: bool = False) -> str:
    renaming: dict[str, str] = {}
    result: list[str] = []
    for value in tokens(strip_postcondition(source)):
        if NONDET_RE.fullmatch(value):
            result.append("NONDET")
        elif IDENT_RE.fullmatch(value) and value not in C_KEYWORDS:
            if value not in renaming:
                renaming[value] = f"v{len(renaming)}"
            result.append(renaming[value])
        elif abstract_constants and INTEGER_RE.fullmatch(value):
            integer = int(value, 0)
            result.append(value if integer in (0, 1) else "NUM")
        else:
            result.append(value)
    return " ".join(result)


def matching_delimiter(source: str, start: int, left: str, right: str) -> int:
    depth = 0
    for index in range(start, len(source)):
        if source[index] == left:
            depth += 1
        elif source[index] == right:
            depth -= 1
            if depth == 0:
                return index
    return len(source) - 1


def first_loop(source: str) -> tuple[str, str]:
    source = remove_noncontract_comments(strip_postcondition(source))
    match = re.search(r"\bwhile\s*\(", source)
    if match is None:
        return "", source
    condition_start = source.find("(", match.start())
    condition_end = matching_delimiter(source, condition_start, "(", ")")
    guard = source[condition_start + 1 : condition_end]
    body_start = source.find("{", condition_end)
    if body_start < 0:
        return guard, source[condition_end + 1 :]
    body_end = matching_delimiter(source, body_start, "{", "}")
    return guard, source[body_start + 1 : body_end]


def conditions(source: str, keyword: str) -> list[str]:
    result: list[str] = []
    for match in re.finditer(rf"\b{keyword}\s*\(", source):
        start = source.find("(", match.start())
        end = matching_delimiter(source, start, "(", ")")
        result.append(source[start + 1 : end])
    return result


def coarse_control_profile(source: str) -> tuple[object, ...]:
    guard, body = first_loop(source)
    all_conditions = [guard, *conditions(body, "if")]
    cap = lambda count: min(count, 3)
    guard_category = "constant" if re.fullmatch(r"\s*[01]\s*", guard) else "expression"
    return (
        guard_category,
        cap(len(re.findall(r"\bif\b", body))),
        cap(len(re.findall(r"\belse\b", body))),
        cap(sum(bool(re.search(r"&&|\|\|", value)) for value in all_conditions)),
        *(bool(re.search(rf"\b{word}\b", body)) for word in ("break", "continue", "return", "goto")),
        bool(re.search(r"\b(?:while|for)\b", body)),
    )


def ordered_control_skeleton(source: str, keep_condition_ops: bool) -> tuple[str, ...]:
    _, body = first_loop(source)
    values = TOKEN_RE.findall(body)
    result: list[str] = []
    ordinary_block = False

    def flush_ordinary_block() -> None:
        nonlocal ordinary_block
        if ordinary_block:
            result.append("STMT")
            ordinary_block = False

    index = 0
    while index < len(values):
        value = values[index]
        if (
            value in CONTROL_WITH_CONDITION
            and index + 1 < len(values)
            and values[index + 1] == "("
        ):
            flush_ordinary_block()
            result.append(value)
            depth = 0
            cursor = index + 1
            condition: list[str] = []
            while cursor < len(values):
                token = values[cursor]
                depth += int(token == "(") - int(token == ")")
                if cursor > index + 1 and depth > 0:
                    condition.append(token)
                cursor += 1
                if depth == 0:
                    break
            if keep_condition_ops:
                result.extend(
                    [token for token in condition if token in CONDITION_OPS]
                    or ["NO_CONDITION_OP"]
                )
            index = cursor
            continue
        if value in {"else", "break", "continue", "return", "goto", "{", "}"}:
            flush_ordinary_block()
            result.append(value)
        elif value == ";":
            ordinary_block = True
        index += 1
    flush_ordinary_block()
    return tuple(result)


def load_training_sources() -> list[str]:
    rows = pq.read_table(TRAIN_PARQUET, columns=["reward_model"]).to_pylist()
    return [row["reward_model"]["ground_truth"]["raw_code"] for row in rows]


def load_sft_sources() -> list[str]:
    records = json.loads(SFT_JSON.read_text(encoding="utf-8"))
    result: list[str] = []
    for record in records:
        human = next(
            turn["value"]
            for turn in record["conversations"]
            if turn["from"] == "human"
        )
        result.append(human.split("Program:\n", 1)[-1])
    return result


def user_template(value: str) -> str:
    prefix, separator, _ = value.partition("Program:\n")
    return f"{prefix}{separator}{{program}}\n" if separator else value


def version_counts(values: Iterable[str]) -> list[dict[str, object]]:
    return [
        {"sha256": text_sha256(value), "characters": len(value), "rows": count}
        for value, count in sorted(
            Counter(values).items(), key=lambda item: (-item[1], text_sha256(item[0]))
        )
    ]


def prompt_version_audit() -> dict[str, object]:
    rl_rows = pq.read_table(TRAIN_PARQUET, columns=["prompt"]).to_pylist()
    rl_system = [
        next(turn["content"] for turn in row["prompt"] if turn["role"] == "system")
        for row in rl_rows
    ]
    rl_user = [
        user_template(
            next(turn["content"] for turn in row["prompt"] if turn["role"] == "user")
        )
        for row in rl_rows
    ]
    sft_rows = json.loads(SFT_JSON.read_text(encoding="utf-8"))
    sft_system = [
        next(turn["value"] for turn in row["conversations"] if turn["from"] == "system")
        for row in sft_rows
    ]
    sft_user = [
        user_template(
            next(turn["value"] for turn in row["conversations"] if turn["from"] == "human")
        )
        for row in sft_rows
    ]
    sft_target_clause_counts = [
        len(
            re.findall(
                r"(?m)^\s*loop invariant\b",
                next(
                    turn["value"]
                    for turn in row["conversations"]
                    if turn["from"] == "gpt"
                ),
            )
        )
        for row in sft_rows
    ]
    rl_visible_sources = [
        next(turn["content"] for turn in row["prompt"] if turn["role"] == "user")
        .split("Program:\n", 1)[-1]
        for row in rl_rows
    ]
    rl_full_sources = load_training_sources()
    sft_visible_sources = load_sft_sources()

    def target_like_comment(visible: str, full: str) -> bool:
        """Conservative lexical overlap between a comment and a removed Q.

        The check requires a predicate-looking ordinary comment and exactly
        the same identifier/integer vocabulary as an ACSL assert or ensures
        clause.  It deliberately ignores comparison-operator differences, so
        comments such as ``0 <= i < 10`` flag a target ``0 <= i <= 10``.
        """
        targets = [
            expression
            for keyword in ("assert", "ensures")
            for _, _, expression in iter_acsl_clauses(full, keyword)
        ]
        target_vocab = [
            {
                token
                for token in re.findall(r"[A-Za-z_]\w*|\d+", expression)
                if token not in C_KEYWORDS
            }
            for expression in targets
        ]
        for match in NONCONTRACT_COMMENT_RE.finditer(visible):
            comment = (match.group(1) or match.group(2) or "").strip()
            if not re.search(r"==|!=|<=|>=|<|>|\binvariant\b", comment, re.I):
                continue
            vocabulary = {
                token
                for token in re.findall(r"[A-Za-z_]\w*|\d+", comment)
                if token.lower() not in {"at", "invariant", "loop"}
                and token not in C_KEYWORDS
            }
            if any(vocabulary and vocabulary == target for target in target_vocab):
                return True
        return False

    def contains_masked_target(source: str) -> bool:
        return normalize_whitespace(
            strip_noncontract_comments(source)
        ) != normalize_whitespace(
            strip_noncontract_comments(strip_postcondition(source))
        )

    return {
        "canonical_system_sha256": text_sha256(SYSTEM_PROMPT.read_text(encoding="utf-8")),
        "canonical_user_template_sha256": text_sha256(USER_PROMPT.read_text(encoding="utf-8")),
        "rl_system_versions": version_counts(rl_system),
        "rl_user_template_versions": version_counts(rl_user),
        "sft_system_versions": version_counts(sft_system),
        "sft_user_template_versions": version_counts(sft_user),
        "sft_target_clauses": {
            "maximum": max(sft_target_clause_counts),
            "rows_above_20": sum(count > 20 for count in sft_target_clause_counts),
        },
        "target_mask_audit": {
            "rl_visible_prompt_sources_with_target": sum(
                contains_masked_target(source) for source in rl_visible_sources
            ),
            "rl_archival_full_sources_with_target": sum(
                contains_masked_target(source) for source in rl_full_sources
            ),
            "sft_visible_prompt_sources_with_target": sum(
                contains_masked_target(source) for source in sft_visible_sources
            ),
        },
        "training_prompt_comment_audit": {
            "rl_visible_prompt_sources": len(rl_visible_sources),
            "rl_visible_prompt_sources_with_noncontract_comment": sum(
                strip_noncontract_comments(source) != source
                for source in rl_visible_sources
            ),
            "rl_visible_prompt_sources_with_target_like_comment": sum(
                target_like_comment(visible, full)
                for visible, full in zip(rl_visible_sources, rl_full_sources)
            ),
            "sft_visible_prompt_sources": len(sft_visible_sources),
            "sft_visible_prompt_sources_with_noncontract_comment": sum(
                strip_noncontract_comments(source) != source
                for source in sft_visible_sources
            ),
        },
    }


def provenance_audit() -> dict[str, object]:
    rows = pq.read_table(
        TRAIN_PARQUET, columns=["data_source", "extra_info"]
    ).to_pylist()
    source_labels = Counter(row["data_source"] for row in rows)
    file_ids = [
        (row.get("extra_info") or {}).get("file_id")
        for row in rows
        if (row.get("extra_info") or {}).get("file_id")
    ]
    return {
        "data_source_labels": dict(sorted(source_labels.items())),
        "rows_with_corpus_local_file_id": len(file_ids),
        "unique_corpus_local_file_ids": len(set(file_ids)),
        "upstream_source_field_present": False,
    }


def load_evaluation_sources() -> tuple[list[Path], list[str]]:
    paths = sorted(
        (path for directory in EVAL_DIRS for path in directory.glob("*.c")),
        key=lambda path: (path.parent.name, int(path.stem)),
    )
    return paths, [path.read_text(encoding="utf-8") for path in paths]


def evaluation_prompt_sanitation_audit(
    paths: Sequence[Path], evaluation: Sequence[str],
) -> dict[str, object]:
    provenance = re.compile(r"(?m)^\s*//\s*Source:")
    outcome = re.compile(
        r"(?im)^\s*//\s*Source:.*(?:true-unreach|false-unreach|"
        r"_safe|_unsafe|_ok|_false|_true)"
    )
    visible = [strip_postcondition(source) for source in evaluation]
    strata: dict[str, dict[str, int]] = {}
    for path, source, hidden in zip(paths, evaluation, visible):
        counts = strata.setdefault(
            path.parent.name,
            {
                "files": 0,
                "raw_with_noncontract_comment": 0,
                "raw_with_outcome_label_in_provenance": 0,
                "raw_with_provenance_comment": 0,
                "visible_with_noncontract_comment": 0,
            },
        )
        counts["files"] += 1
        counts["raw_with_noncontract_comment"] += int(
            strip_noncontract_comments(source) != source
        )
        counts["raw_with_outcome_label_in_provenance"] += int(
            bool(outcome.search(source))
        )
        counts["raw_with_provenance_comment"] += int(
            bool(provenance.search(source))
        )
        counts["visible_with_noncontract_comment"] += int(
            strip_noncontract_comments(hidden) != hidden
        )
    return {
        "by_stratum": dict(sorted(strata.items())),
        "raw_sources_with_noncontract_comment": sum(
            strip_noncontract_comments(source) != source for source in evaluation
        ),
        "raw_sources_with_provenance_comment": sum(
            bool(provenance.search(source)) for source in evaluation
        ),
        "raw_sources_with_outcome_label_in_provenance": sum(
            bool(outcome.search(source)) for source in evaluation
        ),
        "visible_sources_with_provenance_comment": sum(
            bool(provenance.search(source)) for source in visible
        ),
        "visible_sources_with_outcome_label_in_provenance": sum(
            bool(outcome.search(source)) for source in visible
        ),
        "visible_sources_with_noncontract_comment": sum(
            strip_noncontract_comments(source) != source for source in visible
        ),
    }


def compare(
    training: Sequence[str],
    evaluation: Sequence[str],
    signature: Callable[[str], object],
) -> dict[str, object]:
    training_signatures = {signature(source) for source in training}
    evaluation_signatures = [signature(source) for source in evaluation]
    matched = sum(value in training_signatures for value in evaluation_signatures)
    return {
        "shared_signatures": len(training_signatures & set(evaluation_signatures)),
        "matched_evaluation_files": matched,
        "matched_rate": matched / len(evaluation_signatures),
    }


def corpus_sha256(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(ROOT).as_posix().encode()
        digest.update(relative + b"\0" + bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    training = load_training_sources()
    sft = load_sft_sources()
    evaluation_paths, evaluation = load_evaluation_sources()
    normalized_training = {normalize_whitespace(source) for source in training}
    normalized_sft = {normalize_whitespace(source) for source in sft}

    result = {
        "schema_version": 4,
        "inputs": {
            "training_rows": len(training),
            "training_unique_source_strings": len(normalized_training),
            "sft_rows": len(sft),
            "sft_unique_sources": len(normalized_sft),
            "sft_sources_in_training": len(normalized_sft & normalized_training),
            "evaluation_files": len(evaluation),
            "evaluation_by_stratum": {
                directory.name: len(list(directory.glob("*.c")))
                for directory in EVAL_DIRS
            },
            "training_parquet_sha256": sha256(TRAIN_PARQUET),
            "sft_json_sha256": sha256(SFT_JSON),
            "evaluation_corpus_sha256": corpus_sha256(evaluation_paths),
        },
        "representations": {
            "target_hidden_source": compare(training, evaluation, target_hidden_source),
            "near_exact_tokens": compare(training, evaluation, near_exact_tokens),
            "alpha_normalized_source": compare(training, evaluation, alpha_normalized),
            "constant_abstracted_alpha_source": compare(
                training,
                evaluation,
                lambda source: alpha_normalized(source, abstract_constants=True),
            ),
            "coarse_control_flow_profile": compare(
                training, evaluation, coarse_control_profile
            ),
            "ordered_control_flow_skeleton": compare(
                training,
                evaluation,
                lambda source: ordered_control_skeleton(source, False),
            ),
            "skeleton_with_condition_operator_shape": compare(
                training,
                evaluation,
                lambda source: ordered_control_skeleton(source, True),
            ),
        },
        "prompt_versions": prompt_version_audit(),
        "evaluation_prompt_sanitation": evaluation_prompt_sanitation_audit(
            evaluation_paths, evaluation
        ),
        "provenance": provenance_audit(),
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
