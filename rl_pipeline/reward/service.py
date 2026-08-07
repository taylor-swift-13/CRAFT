"""
Reward service — Component 2 exposed over HTTP (FastAPI).

  POST /reward           {program, rollouts, ...}       -> per-rollout reward + batch score
  POST /sample           {program, ...}                 -> example-set stats (debug)
  GET  /health

The example set (positives/negative candidates) is expensive to sample, so it is cached
per (program, sampler-config). Any RL trainer can POST rollout batches here
without a local frama-c or an rl_pipeline import.

Run:  python -m rl_pipeline.reward.service --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from ..common.state import MAX_INVARIANTS_PER_RESPONSE
from ..sampler import ExampleSampler, ExampleSet
from ..sampler.example_sampler import DEFAULT_N_RUNS, DEFAULT_SEED
from . import filters
from .reward_calculator import RewardCalculator

log = logging.getLogger("rl_pipeline.reward.service")

# lazily-built shared invariant filter (Houdini if frama-c present, else positive)
_SHARED_FILTER = None
_EXAMPLE_CACHE: Dict[str, ExampleSet] = {}


def _get_filter():
    global _SHARED_FILTER
    if _SHARED_FILTER is None:
        _SHARED_FILTER = filters.auto_filter(log)
    return _SHARED_FILTER


class SamplerCfg(BaseModel):
    # Defaults come from the canonical sampler source and are shared by the
    # reward service and offline scorer. Inference performs no reward sampling.
    # Synthetic negatives derive only from loop traces, never from the assert.
    n_runs: int = Field(DEFAULT_N_RUNS, ge=1)
    seed: int = DEFAULT_SEED


class RewardRequest(BaseModel):
    program: str = Field(..., description="C program source with requires/loop/assert")
    rollouts: List[Any] = Field(..., description="each: {'invariants':[...]} or {'code': '...'}")
    w_base: float = Field(1.0, ge=0.0)
    w_hard: float = Field(0.3, ge=0.0)
    w_redundancy: float = Field(0.02, ge=0.0)
    w_overflow: float = Field(0.05, ge=0.0)
    max_invariants: int = Field(
        MAX_INVARIANTS_PER_RESPONSE,
        ge=1,
        le=MAX_INVARIANTS_PER_RESPONSE,
    )
    reroll_threshold: float = Field(0.6, ge=0.0, le=1.0)
    sampler: SamplerCfg = Field(default_factory=SamplerCfg)


class SampleRequest(BaseModel):
    program: str
    sampler: SamplerCfg = Field(default_factory=SamplerCfg)
    show: int = Field(8, ge=0)


def _cache_key(program: str, n_runs: int, seed: int) -> str:
    return hashlib.sha1(f"{program}|runs={n_runs}|seed={seed}".encode()).hexdigest()


def _get_examples(program: str, cfg: SamplerCfg):
    key = _cache_key(program, cfg.n_runs, cfg.seed)
    es = _EXAMPLE_CACHE.get(key)
    if es is None:
        es = ExampleSampler(program, n_runs=cfg.n_runs, seed=cfg.seed).sample()
        _EXAMPLE_CACHE[key] = es
    return es


def build_app():
    from fastapi import FastAPI, HTTPException

    app = FastAPI(title="LoopGym Reward Service", version="1.0")

    @app.get("/health")
    def health():
        return {"status": "ok", "filter_mode": getattr(_get_filter(), "name", "unknown"),
                "cached_programs": len(_EXAMPLE_CACHE)}

    @app.post("/reward")
    def reward(req: RewardRequest):
        try:
            examples = _get_examples(req.program, req.sampler)
            rc = RewardCalculator(
                invariant_filter=_get_filter(),
                w_base=req.w_base, w_hard=req.w_hard,
                w_redundancy=req.w_redundancy,
                w_overflow=req.w_overflow,
                max_invariants=req.max_invariants,
                reroll_threshold=req.reroll_threshold, logger=log,
            )
            br = rc.compute(req.program, req.rollouts, examples=examples)
            return br.to_dict()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/sample")
    def sample(req: SampleRequest):
        try:
            es = _get_examples(req.program, req.sampler)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        out = {"program": es.program.func_name, "guard": es.program.loop.guard,
               "loops": {}}
        for li in sorted(es.positives):
            out["loops"][li] = {
                "stats": es.stats.get(li, {}),
                "positives": [s.render() for s in es.pos(li)[:req.show]],
                "negatives": [s.render() for s in es.neg(li)[:req.show]],
            }
        return out

    return app


# Module-level app for `uvicorn rl_pipeline.reward.service:app`.
app = build_app()


def _main():
    import argparse
    import uvicorn

    ap = argparse.ArgumentParser(description="Run the reward HTTP service")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    uvicorn.run(build_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    _main()
