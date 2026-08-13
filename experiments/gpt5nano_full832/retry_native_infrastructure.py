"""Retry native-tool attempts that failed before receiving a usable response.

This deliberately excludes verifier failures, native search exhaustion, and
benchmark timeouts.  It only retries logs with explicit transport/service
failure evidence so the experiment does not turn genuine tool failures into
unreported stochastic rerolls.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import re

from .common import Task, append_jsonl, base_row, discover_tasks, latest_rows
from .native import run_autospec, run_clause2inv, run_sespec
from .run import event_path, new_attempt_dir


METHODS = ("autospec", "clause2inv", "sespec")
INFRASTRUCTURE_FAILURE = re.compile(
    r"(?:"
    r"RateLimitError|APIConnectionError|APITimeoutError|InternalServerError|"
    r"ConnectError|RemoteProtocolError|Server disconnected|"
    r"TLS/SSL connection has been closed|Connection error\.|Request timed out\.|"
    r"HTTP/1\.1 (?:429|5\d\d)"
    r")",
    re.IGNORECASE,
)


def _command_log(row: dict) -> Path | None:
    hidden = row.get("hidden_source")
    if hidden:
        path = Path(hidden).parent / "command.log"
        if path.is_file():
            return path
    artifact = row.get("artifact")
    if artifact:
        path = Path(artifact).parent.parent / "command.log"
        if path.is_file():
            return path
    return None


def _retryable(row: dict) -> bool:
    if row.get("generation_status") != "failed":
        return False
    log = _command_log(row)
    return bool(
        log
        and INFRASTRUCTURE_FAILURE.search(log.read_text(errors="ignore"))
    )


def _run_one(
    method: str,
    task: Task,
    root: Path,
    *,
    autospec_root: Path,
    clause2inv_root: Path,
    sespec_root: Path,
) -> dict:
    directory = new_attempt_dir(root, method, task)
    if method == "autospec":
        generated = run_autospec(
            task, directory, autospec_root=autospec_root, timeout=600
        )
    elif method == "clause2inv":
        generated = run_clause2inv(
            task, directory, clause2inv_root=clause2inv_root, timeout=7200
        )
    elif method == "sespec":
        generated = run_sespec(
            task, directory, sespec_root=sespec_root, timeout=7200
        )
    else:  # pragma: no cover - guarded by argparse
        raise ValueError(method)
    row = base_row(method, task)
    row.update({
        "generation_eligible": True,
        "infrastructure_retry": True,
        **generated,
    })
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--methods", nargs="+", choices=METHODS, default=METHODS)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--autospec-root", type=Path,
        default=Path("/home/yangfp/TRASH/SESpecTrash/represent/external/autospec"),
    )
    parser.add_argument(
        "--clause2inv-root", type=Path,
        default=Path("/home/yangfp/Clause2Inv"),
    )
    parser.add_argument(
        "--sespec-root", type=Path,
        default=Path("/home/yangfp/SESpec"),
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    tasks = {(task.suite, task.case_id): task for task in discover_tasks()}
    work: list[tuple[str, Task]] = []
    for method in args.methods:
        latest = latest_rows([event_path(args.results_root, method)])
        selected = []
        for row in latest.values():
            task = tasks.get((str(row.get("suite")), str(row.get("case_id"))))
            if task is not None and _retryable(row):
                selected.append(task)
        selected.sort(key=lambda task: (task.suite, int(task.case_id)))
        print(f"{method}: infrastructure retries={len(selected)}", flush=True)
        work.extend((method, task) for task in selected)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                _run_one,
                method,
                task,
                args.results_root,
                autospec_root=args.autospec_root,
                clause2inv_root=args.clause2inv_root,
                sespec_root=args.sespec_root,
            ): (method, task)
            for method, task in work
        }
        for index, future in enumerate(as_completed(futures), 1):
            method, task = futures[future]
            row = future.result()
            append_jsonl(event_path(args.results_root, method), row)
            print(
                f"[{index}/{len(work)}] {method} {task.suite}/{task.case_id} "
                f"{row.get('generation_status')}",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
