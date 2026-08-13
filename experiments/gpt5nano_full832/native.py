from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from rl_pipeline.common.program import parse_program
from rl_pipeline.common.state import extract_invariants

from .common import Task, token_fields


def redact_secrets(text: str) -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        text = text.replace(key, "<REDACTED_OPENAI_API_KEY>")
    return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "<REDACTED_API_KEY>", text)


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict,
    timeout: int,
) -> tuple[int | None, bool, str, str, float]:
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    def stop_process_group() -> tuple[str, str]:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
        return stdout or "", stderr or ""

    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return (
            process.returncode,
            False,
            stdout or "",
            stderr or "",
            time.perf_counter() - started,
        )
    except subprocess.TimeoutExpired as error:
        stdout, stderr = stop_process_group()
        stdout = stdout or error.stdout or ""
        stderr = stderr or error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return None, True, stdout, stderr, time.perf_counter() - started
    except BaseException:
        stop_process_group()
        raise


def _result_json(stdout: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith("RESULT_JSON:"):
            try:
                return json.loads(line[len("RESULT_JSON:"):])
            except json.JSONDecodeError:
                return None
    return None


def run_clause2inv(
    task: Task,
    artifact_dir: Path,
    *,
    clause2inv_root: Path,
    timeout: int = 7200,
) -> dict:
    """Run native Clause2Inv with the postcondition hidden from the model/search."""
    if task.suite == "Loopy":
        return {
            "generation_status": "unsupported",
            "generation_error": (
                "native Clause2Inv requires precomputed Code2Inv SMT transition "
                "VCs; the Loopy corpus has no compatible VCs"
            ),
            "invariants": [],
            "generation_seconds": 0.0,
            "target_hidden": True,
            **token_fields(
                prompt=0, completion=0, total=0, calls=0,
                accounting="not_called",
            ),
        }

    combinator = clause2inv_root / "combinator"
    if task.suite == "linear":
        code = combinator / "Benchmarks" / "Linear" / "c" / f"{task.case_id}.c"
        graph = combinator / "Benchmarks" / "Linear" / "c_graph" / f"{task.case_id}.c.json"
        vcs = combinator / "Benchmarks" / "Linear" / "c_smt2" / f"{task.case_id}.c.smt"
    elif task.suite == "NLA_lipus":
        code = combinator / "Benchmarks" / "NL" / "c" / f"NL{task.case_id}.c"
        graph = combinator / "Benchmarks" / "NL" / "c_graph" / f"NL{task.case_id}.c.json"
        vcs = combinator / "Benchmarks" / "NL" / "c_smt" / f"NL{task.case_id}.c_smt"
    else:
        raise ValueError(f"unsupported Clause2Inv suite: {task.suite}")

    # The upstream CLI keeps ``-input_graph`` for compatibility, but its
    # checker never reads that path.  Some distributions therefore omit the
    # graph directory entirely; code and SMT VCs are the required inputs.
    missing = [str(path) for path in (code, vcs) if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing Clause2Inv input: " + ", ".join(missing))

    artifact_dir = artifact_dir.resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    hidden = artifact_dir / "input.hidden.c"
    hidden.write_text(task.hidden_source)
    clause2inv_python = Path(os.environ.get(
        "CLAUSE2INV_PYTHON",
        "/home/yangfp/miniconda3/envs/clause2inv/bin/python",
    ))
    if not clause2inv_python.is_file():
        clause2inv_python = Path(sys.executable)
    command = [
        str(clause2inv_python), "-u", str(combinator / "main.py"),
        "-input_code", str(hidden),
        "-input_graph", str(graph),
        "-input_vcs", str(vcs),
        "-target_hidden",
        "-model", "gpt-5-nano",
    ]
    env = os.environ.copy()
    env.update({
        "CLAUSE2INV_MODEL": "gpt-5-nano",
        "REASONING_EFFORT": "none",
    })
    returncode, timed_out, stdout, stderr, wall = _run(
        command, cwd=combinator, env=env, timeout=timeout
    )
    (artifact_dir / "command.log").write_text(
        redact_secrets(
            f"$ {' '.join(command)}\n\n[stdout]\n{stdout}\n\n[stderr]\n{stderr}"
        )
    )
    parsed = _result_json(stdout)
    usage = parsed.get("api_usage") if parsed else None
    invariant = parsed.get("invariant") if parsed else None
    return {
        "generation_status": (
            "timeout" if timed_out
            else "completed" if returncode == 0 and parsed is not None
            else "failed"
        ),
        "generation_error": (
            "timeout" if timed_out
            else "missing_result_json" if returncode == 0 and parsed is None
            else None if returncode == 0
            else f"returncode_{returncode}"
        ),
        "returncode": returncode,
        "timeout": timed_out,
        "target_hidden": True,
        "hidden_source": str(hidden),
        "native_candidate_found": bool(parsed and parsed.get("candidate_found")),
        "native_verified": parsed.get("target_verified") if parsed else None,
        "invariants": [invariant] if invariant else [],
        "generation_seconds": wall,
        "generation_timeout_seconds": timeout,
        "reasoning_tokens": usage.get("reasoning_tokens") if usage else None,
        **token_fields(
            prompt=usage.get("prompt_tokens") if usage else None,
            completion=usage.get("completion_tokens") if usage else None,
            total=usage.get("total_tokens") if usage else None,
            calls=usage.get("api_calls") if usage else None,
            accounting="exact" if usage else "unavailable",
        ),
    }


_autospec_local = threading.local()
_autospec_roots: list[Path] = []
_autospec_guard = threading.Lock()


def _autospec_worker_root(source_root: Path) -> Path:
    root = getattr(_autospec_local, "root", None)
    if root is not None:
        return root
    parent = Path(tempfile.mkdtemp(prefix="craft_autospec_worker_"))
    root = parent / "autospec"
    shutil.copytree(
        source_root,
        root,
        symlinks=True,
        copy_function=os.link,
        ignore=shutil.ignore_patterns(
            "out", "*.pickle", "*_infilled.c", "*_marked.c", "tmp*.o"
        ),
    )
    for stale in root.glob("tmp*.o"):
        stale.unlink(missing_ok=True)
    _autospec_local.root = root
    with _autospec_guard:
        _autospec_roots.append(parent)
    return root


@atexit.register
def _cleanup_autospec_workers() -> None:
    for root in _autospec_roots:
        shutil.rmtree(root, ignore_errors=True)


def run_autospec(
    task: Task,
    artifact_dir: Path,
    *,
    autospec_root: Path,
    timeout: int = 600,
) -> dict:
    worker = _autospec_worker_root(autospec_root)
    # AutoSpec runs with its temporary worker checkout as cwd.  Keep the
    # result directory absolute so a caller-provided relative --results-root
    # cannot redirect generated files into that disposable checkout.
    artifact_dir = artifact_dir.resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    hidden = artifact_dir / "input.hidden.c"
    hidden.write_text(task.hidden_source)
    bench = worker / "benchmark" / "craft_full832_hidden" / task.suite
    bench.mkdir(parents=True, exist_ok=True)
    staged = bench / f"{task.case_id}.c"
    shutil.copyfile(hidden, staged)
    output = artifact_dir / "autospec_out"
    output.mkdir(parents=True, exist_ok=True)

    command = [
        "conda", "run", "--no-capture-output", "-n", "SpecAutoGen",
        "python", "step.py",
        "-f", str(staged),
        "-t", "0",
        "-o", str(output),
        "-m", "gpt-5-nano",
    ]
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(worker),
        "ROOT_DIR": str(worker),
        "LLVM_COMPILER": "clang",
        "VERI_LIB_PATH": str(worker / "llvm"),
        "AUTOSPEC_FRAMAC_MAX_ATTEMPTS": "16",
        "AUTOSPEC_FRAMAC_WALL_TIMEOUT": "60",
        "AUTOSPEC_MAX_TOKENS": "8192",
        "REASONING_EFFORT": "none",
        "PATH": (
            f"/home/yangfp/.opam/frama-c.27.1/bin:"
            f"{worker / 'llvm'}:{worker / 'llvm' / 'bin'}:"
            f"{worker / 'clang+llvm' / 'bin'}:{env.get('PATH', '')}"
        ),
    })
    if os.environ.get("OPENAI_BASE_URL"):
        env["API_URL_BASE"] = os.environ["OPENAI_BASE_URL"]
    returncode, timed_out, stdout, stderr, wall = _run(
        command, cwd=worker, env=env, timeout=timeout
    )
    (artifact_dir / "command.log").write_text(
        redact_secrets(
            f"$ {' '.join(command)}\n\n[stdout]\n{stdout}\n\n[stderr]\n{stderr}"
        )
    )
    merged = next(iter(sorted(output.glob("*_merged.c"))), None)
    invariants = extract_invariants(
        merged.read_text(errors="ignore") if merged else ""
    )
    usage_matches = re.findall(r"AUTOSPEC_API_USAGE=(\{[^\n]+\})", stdout)
    api_usage_rows = [json.loads(match) for match in usage_matches]
    api_usage = (
        {
            name: sum(int(row.get(name) or 0) for row in api_usage_rows)
            for name in (
                "prompt_tokens", "completion_tokens", "reasoning_tokens",
                "total_tokens",
            )
        }
        if api_usage_rows else None
    )
    token_match = re.search(r"tokens_usage\s*=\s*([\d,]+)", stdout)
    native_tokens = int(token_match.group(1).replace(",", "")) if token_match else None
    if api_usage:
        usage_fields = token_fields(
            prompt=api_usage.get("prompt_tokens"),
            completion=api_usage.get("completion_tokens"),
            total=api_usage.get("total_tokens"),
            calls=len(api_usage_rows),
            accounting="exact",
        )
        token_estimate_kind = None
    else:
        usage_fields = token_fields(
            total=native_tokens,
            accounting="estimated" if native_tokens is not None else "unavailable",
        )
        token_estimate_kind = (
            "AutoSpec native tiktoken estimate" if native_tokens is not None else None
        )
    return {
        "generation_status": (
            "timeout" if timed_out
            else "completed" if returncode == 0 and merged is not None
            else "failed"
        ),
        "generation_error": (
            "timeout" if timed_out
            else "missing_merged_artifact" if returncode == 0 and merged is None
            else None if returncode == 0
            else f"returncode_{returncode}"
        ),
        "returncode": returncode,
        "timeout": timed_out,
        "hidden_source": str(hidden),
        "artifact": str(merged) if merged else None,
        "invariants": invariants,
        "generation_seconds": wall,
        "generation_timeout_seconds": timeout,
        "autospec_framac_max_attempts": int(
            env["AUTOSPEC_FRAMAC_MAX_ATTEMPTS"]
        ),
        "autospec_framac_wall_timeout_seconds": int(
            env["AUTOSPEC_FRAMAC_WALL_TIMEOUT"]
        ),
        "reasoning_tokens": api_usage.get("reasoning_tokens") if api_usage else None,
        "token_estimate_kind": token_estimate_kind,
        **usage_fields,
    }


_WORKSPACE_RE = re.compile(r"workspace_root=(\S+)")
_TOKEN_RES = {
    "prompt_tokens": re.compile(r"Total prompt tokens[^:]*:\s*([\d,]+)"),
    "completion_tokens": re.compile(r"Total completion tokens[^:]*:\s*([\d,]+)"),
    "total_tokens": re.compile(r"Total tokens:\s*([\d,]+)"),
    "api_call_count": re.compile(r"Total API calls:\s*(\d+)"),
}


def _match_int(regex: re.Pattern, text: str):
    match = regex.search(text)
    return int(match.group(1).replace(",", "")) if match else None


def run_sespec(
    task: Task,
    artifact_dir: Path,
    *,
    sespec_root: Path,
    timeout: int = 7200,
) -> dict:
    # SESpec changes its working directory to ``src`` before loading the
    # configuration.  Resolve campaign artifacts up front so the config,
    # hidden source, and copied outputs remain addressable from that cwd.
    artifact_dir = artifact_dir.resolve()
    src = sespec_root / "src"
    protocol_tag = "craft_full832_hidden_v1"
    staged_dir = src / "input" / protocol_tag / task.suite
    staged_dir.mkdir(parents=True, exist_ok=True)
    staged = staged_dir / f"{task.case_id}.c"
    staged.write_text(task.hidden_source)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    hidden = artifact_dir / "input.hidden.c"
    hidden.write_text(task.hidden_source)

    function_name = parse_program(task.hidden_source).func_name
    config = {
        "main": {
            "root_dir": f"{protocol_tag}/{task.suite}",
            "function_name": function_name,
            "auto_annotation": True,
            "pass_count": 1,
            "refine_count": 3,
            "think": False,
            "use_se": True,
            "debug": False,
        },
        "llm": {
            "api_model": "gpt-5-nano",
            "api_temperature": 1.0,
            "api_top_p": 1.0,
            "max_completion_tokens": 8192,
            "reasoning_effort": "none",
            "think_mode_enabled": False,
        },
        "preconditions": {function_name: "emp"},
    }
    config_path = artifact_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2))
    command = [
        str(Path("/home/yangfp/miniconda3/envs/SpecAutoGen/bin/python")),
        "main.py", "--config", str(config_path),
    ]
    env = os.environ.copy()
    env["PATH"] = f"/home/yangfp/.opam/frama-c.27.1/bin:{env.get('PATH', '')}"
    returncode, timed_out, stdout, stderr, wall = _run(
        command, cwd=src, env=env, timeout=timeout
    )
    (artifact_dir / "command.log").write_text(
        redact_secrets(
            f"$ {' '.join(command)}\n\n[stdout]\n{stdout}\n\n[stderr]\n{stderr}"
        )
    )

    workspace_match = _WORKSPACE_RE.search(stdout)
    workspace = Path(workspace_match.group(1)) if workspace_match else None
    copied = {}
    if workspace and workspace.is_dir():
        candidates = {
            "output": workspace / "output" / f"{function_name}.c",
            "qcp": workspace / "2_output" / f"{function_name}.c",
            "loop": workspace / "3_output" / f"{function_name}.c",
            "native_log": workspace / "run.log",
        }
        for label, source in candidates.items():
            if source.exists():
                destination = artifact_dir / f"{label}{source.suffix}"
                shutil.copyfile(source, destination)
                copied[label] = destination
    candidate = copied.get("output") or copied.get("loop") or copied.get("qcp")
    invariants = extract_invariants(
        candidate.read_text(errors="ignore") if candidate else ""
    )
    usage = {name: _match_int(regex, stdout) for name, regex in _TOKEN_RES.items()}
    exact = usage["total_tokens"] is not None
    return {
        "generation_status": (
            "timeout" if timed_out
            else "completed" if returncode == 0
            else "failed"
        ),
        "generation_error": (
            "timeout" if timed_out
            else None if returncode == 0
            else f"returncode_{returncode}"
        ),
        "returncode": returncode,
        "timeout": timed_out,
        "hidden_source": str(hidden),
        "artifact": str(candidate) if candidate else None,
        "invariants": invariants,
        "generation_seconds": wall,
        **token_fields(
            prompt=usage["prompt_tokens"],
            completion=usage["completion_tokens"],
            total=usage["total_tokens"],
            calls=usage["api_call_count"],
            accounting="exact" if exact else "unavailable",
        ),
    }
