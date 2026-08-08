"""Deploy a trained model to vLLM and run certified inference.

    python -m rl_pipeline.inference --model <hf-or-local> \
        --inputs 'src/input/NLA_lipus/*.c' --n-rollouts 8 --output results.jsonl

Inference does not sample reward examples. It generates with vLLM, combines
the rollouts, then runs Houdini and Frama-C/WP verification.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import glob
import json
import logging
import os
import threading

from . import InferenceFramework, LLMRolloutProvider, VLLMRolloutProvider


def _expand(patterns):
    out = []
    seen = set()
    for p in patterns:
        if os.path.isdir(p):
            matches = sorted(glob.glob(os.path.join(p, "**", "*.c"), recursive=True))
        elif any(c in p for c in "*?["):
            matches = sorted(glob.glob(p, recursive=True))
        else:
            matches = [p]
        for match in matches:
            key = os.path.abspath(match)
            if key not in seen:
                seen.add(key)
                out.append(match)
    return out


def main():
    ap = argparse.ArgumentParser(description="Run certified target-hidden inference")
    ap.add_argument("--model", required=True, help="HF id or local dir of the trained model")
    ap.add_argument("--inputs", nargs="+", required=True, help="dirs / globs / files of .c programs")
    ap.add_argument("--backend", choices=("vllm", "api"), default="vllm",
                    help="in-process vLLM or an OpenAI-compatible API")
    ap.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"),
                    help="OpenAI-compatible API URL (api backend)")
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel programs; API backend only")
    ap.add_argument("--n-rollouts", type=int, default=8)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--top-p", type=float, default=1.0)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--output", default=None, help="write per-program results as JSONL")
    ap.add_argument("--resume", action="store_true",
                    help="skip inputs already present in the output JSONL")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.workers < 1:
        ap.error("--workers must be at least 1")
    if args.backend == "vllm" and args.workers != 1:
        ap.error("--workers > 1 is supported only by the api backend")
    if args.backend == "api" and not os.environ.get("OPENAI_API_KEY"):
        ap.error("OPENAI_API_KEY is required by the api backend")
    if args.resume and not args.output:
        ap.error("--resume requires --output")

    files = _expand(args.inputs)
    if not files:
        raise SystemExit("no input .c files matched")

    completed = set()
    if args.resume and os.path.exists(args.output):
        with open(args.output) as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("input"):
                    completed.add(os.path.abspath(row["input"]))
        files = [path for path in files if os.path.abspath(path) not in completed]

    local = threading.local()

    def provider():
        if not hasattr(local, "provider"):
            if args.backend == "vllm":
                local.provider = VLLMRolloutProvider(
                    model=args.model,
                    temperature=args.temperature,
                    top_p=args.top_p,
                    max_tokens=args.max_tokens,
                )
            else:
                from src.config import LLMConfig
                from src.llm import Chatbot

                cfg = LLMConfig(
                    api_model=args.model,
                    base_url=args.base_url or LLMConfig.base_url,
                    api_temperature=args.temperature,
                    api_top_p=args.top_p,
                    api_max_tokens=args.max_tokens,
                )
                local.provider = LLMRolloutProvider(chat_fn=Chatbot(cfg).chat)
        return local.provider

    def run_one(path):
        src = open(path).read()
        fw = InferenceFramework(src, rollout_provider=provider(),
                                n_rollouts=args.n_rollouts)
        res = fw.run()
        return {
            "input": path,
            "program": os.path.basename(path),
            "model": args.model,
            "backend": args.backend,
            "target_hidden": True,
            "verified": res.verified,
            "invariants": res.final_invariants,
            "rollouts": res.rollouts,
            "n_rollouts": res.n_rollouts,
        }

    output_handle = open(args.output, "a" if args.resume else "w") if args.output else None
    rows, verified = [], 0
    try:
        if args.workers == 1:
            iterator = ((path, run_one(path)) for path in files)
        else:
            executor = ThreadPoolExecutor(max_workers=args.workers)
            futures = {executor.submit(run_one, path): path for path in files}
            iterator = ((futures[future], future.result())
                        for future in as_completed(futures))

        for i, (path, row) in enumerate(iterator, 1):
            rows.append(row)
            verified += int(row["verified"] is True)
            if output_handle:
                output_handle.write(json.dumps(row) + "\n")
                output_handle.flush()
            logging.info("[%d/%d] %s  verified=%s",
                         i, len(files), os.path.basename(path),
                         row["verified"])
    finally:
        if args.workers > 1 and "executor" in locals():
            executor.shutdown(wait=True)
        if output_handle:
            output_handle.close()

    print(f"\nverified {verified}/{len(files)} newly evaluated (Frama-C/WP)")
    if args.output:
        print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
