"""Rejudge external invariant-generation results with one Frama-C/WP gate.

The generator-facing run must already have hidden the target.  This command
extracts only the generated loop invariants, inserts them into the untouched
LoopGym source (which still contains the original assertion), and records a
common target-bearing verification result.

Examples:
    python -m rl_pipeline.eval.certify_results \
      --method sespec --input /path/to/matrix_linear /path/to/matrix_nla \
      --source-root src/input --output results/sespec_loopgym832_strict/results.jsonl

    python -m rl_pipeline.eval.certify_results \
      --method clause2inv --input results/clause2inv/results.jsonl \
      --source-root src/input --output results/clause2inv/certified.jsonl
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import glob
import json
import os
from pathlib import Path
from typing import Iterable

from ..common.program import parse_program
from ..common.state import dedup_normalized, extract_invariants
from ..inference import InferenceFramework, MockRolloutProvider
from ..reward import annotate


def _numeric_key(value: str):
    return (0, int(value)) if value.isdigit() else (1, value)


def _source_path(source_root: Path, suite: str, case_id: str) -> Path:
    normalized_suite = "NLA_lipus" if suite in {"nonlinear", "nla"} else suite
    return source_root / normalized_suite / f"{case_id}.c"


def _latest_summaries(roots: Iterable[Path]) -> list[Path]:
    latest: dict[tuple[str, str], Path] = {}
    for root in roots:
        for path in root.rglob("summary.json"):
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            suite = str(data.get("bench") or data.get("root_dir") or data.get("suite") or "")
            case_id = str(data.get("case_id") or "")
            if not suite or not case_id:
                continue
            key = (suite, case_id)
            previous = latest.get(key)
            if previous is None or path.stat().st_mtime > previous.stat().st_mtime:
                latest[key] = path
    return sorted(
        latest.values(),
        key=lambda path: (
            json.loads(path.read_text()).get("bench", ""),
            _numeric_key(str(json.loads(path.read_text()).get("case_id", ""))),
        ),
    )


def _load_sespec(roots: list[Path], source_root: Path) -> list[dict]:
    rows = []
    for summary_path in _latest_summaries(roots):
        summary = json.loads(summary_path.read_text())
        suite = str(summary.get("bench") or summary.get("root_dir"))
        case_id = str(summary["case_id"])
        artifact = None
        for key in ("output_path", "loop_acsl_path", "loop_qcp_path"):
            candidate = summary.get(key)
            if candidate and Path(candidate).exists():
                artifact = Path(candidate)
                break
        invariants = extract_invariants(
            artifact.read_text(errors="ignore") if artifact else ""
        )
        rows.append({
            "method": "sespec",
            "suite": suite,
            "case_id": case_id,
            "source": str(_source_path(source_root, suite, case_id)),
            "artifact": str(artifact) if artifact else None,
            "invariants": invariants,
            "generation_success": bool(summary.get("success")),
            "model": summary.get("model", "gpt-5-nano"),
            "target_hidden": True,
            "upstream_summary": str(summary_path),
            "total_seconds": summary.get("total_seconds"),
            "total_tokens": summary.get("total_tokens"),
        })
    return rows


def _load_clause2inv(path: Path, source_root: Path) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        suite, case_id = str(data["task_id"]).split("/", 1)
        invariant = data.get("invariant")
        rows.append({
            "method": "clause2inv",
            "suite": suite,
            "case_id": case_id,
            "source": str(_source_path(source_root, suite, case_id)),
            "artifact": None,
            "invariants": [invariant] if invariant else [],
            "generation_success": bool(data.get("candidate_found")),
            "model": data.get("model", "gpt-5-nano"),
            "target_hidden": bool(data.get("target_hidden")),
            "upstream_target_verified": data.get("target_verified"),
            "total_seconds": data.get("elapsed_seconds"),
            "timeout": data.get("timeout", False),
            "returncode": data.get("returncode"),
        })
    return rows


def _load_autospec(roots: list[Path], source_root: Path) -> list[dict]:
    rows = []
    for summary_path in _latest_summaries(roots):
        summary = json.loads(summary_path.read_text())
        suite = str(summary.get("suite") or summary.get("bench"))
        case_id = str(summary["case_id"])
        candidates = sorted((summary_path.parent / "autospec_out").glob("*_merged.c"))
        artifact = candidates[0] if candidates else None
        invariants = extract_invariants(
            artifact.read_text(errors="ignore") if artifact else ""
        )
        rows.append({
            "method": "autospec",
            "suite": suite,
            "case_id": case_id,
            "source": str(_source_path(source_root, suite, case_id)),
            "artifact": str(artifact) if artifact else None,
            "invariants": invariants,
            "generation_success": artifact is not None,
            "model": summary.get("model", "gpt-5-nano"),
            "target_hidden": bool(summary.get("target_hidden")),
            "upstream_summary": str(summary_path),
            "total_seconds": summary.get("total_seconds"),
            "total_tokens": summary.get("total_tokens"),
            "timeout": summary.get("timeout", False),
            "returncode": summary.get("returncode"),
        })
    return rows


def _certify(row: dict) -> dict:
    result = dict(row)
    source_path = Path(row["source"])
    if not source_path.exists():
        result.update(verified=None, verification_error="missing_source")
        return result
    try:
        source = source_path.read_text(errors="ignore")
        invariants = dedup_normalized(row.get("invariants") or [])
        program = parse_program(source)
        annotated = annotate.build_annotated(program, invariants, 0)
        framework = InferenceFramework(
            source,
            rollout_provider=MockRolloutProvider([[]]),
            n_rollouts=1,
        )
        verified = framework._verify(annotated)
        result.update(
            invariants=invariants,
            invariant_count=len(invariants),
            verified=verified,
            verification_error=None if verified is not None else "frama_c_unavailable_or_failed",
        )
    except Exception as error:
        result.update(
            verified=None,
            verification_error=f"{type(error).__name__}: {error}",
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", choices=("sespec", "clause2inv", "autospec"), required=True)
    parser.add_argument("--input", nargs="+", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path("src/input"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    if args.method == "sespec":
        rows = _load_sespec(args.input, args.source_root)
    elif args.method == "autospec":
        rows = _load_autospec(args.input, args.source_root)
    else:
        if len(args.input) != 1:
            parser.error("clause2inv expects exactly one JSONL input")
        rows = _load_clause2inv(args.input[0], args.source_root)

    completed = set()
    if args.resume and args.output.exists():
        for line in args.output.read_text().splitlines():
            try:
                old = json.loads(line)
            except json.JSONDecodeError:
                continue
            if old.get("suite") and old.get("case_id"):
                completed.add((str(old["suite"]), str(old["case_id"])))
    rows = [
        row for row in rows
        if (str(row["suite"]), str(row["case_id"])) not in completed
    ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.resume else "w"
    verified = 0
    with args.output.open(mode) as handle, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_certify, row): row for row in rows}
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            handle.write(json.dumps(result, sort_keys=True) + "\n")
            handle.flush()
            verified += int(result.get("verified") is True)
            print(
                f"[{index}/{len(rows)}] {result['suite']}/{result['case_id']} "
                f"verified={result.get('verified')}",
                flush=True,
            )
    print(f"verified {verified}/{len(rows)} newly certified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
