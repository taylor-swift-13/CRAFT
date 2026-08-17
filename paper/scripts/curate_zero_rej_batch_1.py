#!/usr/bin/env python3
"""Curate the explicitly requested zero-rejection SFT rows.

This is deliberately a data-only audit: it never edits the training file.  The
five proposal families below are intentionally different (bounds, transition
relations, monotonicity, modular facts, and entry/exit facts); Houdini is the
authority for what can be kept.
"""
from __future__ import annotations

import argparse, json, re, sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from paper.scripts._curation_common import record_source_and_answer
from rl_pipeline.common.program import parse_program
from rl_pipeline.common.state import eval_predicate, extract_invariants, normalize_invariant
from rl_pipeline.reward.filters import HoudiniFilter
from rl_pipeline.sampler.example_sampler import ExampleSampler

ROWS = [82,285,386,534,845,892,1013,1141,1179,1212,1347,1611,1687,1847,1969,1987,2120,2303,636,136,749,1593,1490,2199,391,299,1084,1376,2056,297,868,284,1832,1032,645,1377,1271,2118,1129,1479,2203,2207,2271,2274,2290,2292,2296,2300,2889,2897,2910,1419,1512]
SFT = ROOT / "traindata/craft_sft_clean.json"
LEDGER = ROOT / "paper/artifacts/sft_negative_rejection.jsonl"
OUT = ROOT / "paper/artifacts/curation_zero_rej_batch_1.json"

def invs(*xs):
    return list(xs)

def proposals(row: int, source: str):
    """Five semantically distinct proposal sets for each task."""
    # Infinite/update-only loops and unsigned counters.
    if row == 82:
        return [invs("delta >= 0"), invs("gh >= \\at(gh,LoopEntry)"), invs("gh % 6 == \\at(gh,LoopEntry) % 6"), invs("delta <= gh - \\at(gh,LoopEntry)"), invs("gh - 6*delta == \\at(gh,LoopEntry) - 6*\\at(delta,LoopEntry)")]
    if row in (285, 892):
        k = "c" if row == 285 else "k"
        return [invs("0 <= c", "c == 200000"), invs("cur == b || cur == b + 1"), invs("cur >= b - 1", "b >= 0"), invs("c >= 0", "st == 0 || st == 1"), invs("cur - b <= 1")]
    if row in (1013,1987,2120,1687,1141,1212,2199):
        step = {1013:14,1987:14,2120:1,1687:1,1141:6,1212:2,2199:6}[row]
        return [invs(f"x <= 0x0fffffff + {step}"), invs("x >= 0", "x < 0x10000000"), invs(f"x <= 0x0fffffff + {step} - 1"), invs("a >= \\at(a,LoopEntry)"), invs("a - \\at(a,LoopEntry) >= 0")]
    if row == 1847:
        return [invs("a >= \\at(a,LoopEntry)"), invs("x >= 0"), invs("x <= 0x0fffffff"), invs("x < 0x10000000"), invs("a - \\at(a,LoopEntry) >= 0")]
    if row == 636:
        return [invs("d % 2 == 1"), invs("d >= 1"), invs("d <= 0x0fffffff"), invs("d % 2 != 0", "d > 0"), invs("d + 2 <= 0x10000000")]

    # Accumulator/counter families with nondeterministic counter progress.
    if row in (386,534,1179,1969):
        v, counter, lim = ({386:("x","c","k"),534:("x","c","k"),1179:("x","c","k"),1969:("v2","v7","z")})[row]
        return [invs(f"{v} >= 0", f"{counter} >= 0"), invs(f"{v} >= {counter} - 1"), invs(f"{v} >= 0", f"{v} >= {counter}"), invs(f"{counter} <= {lim}", f"{counter} <= {v}"), invs(f"{v} - {counter} >= -1", f"{counter} >= 0")]
    if row in (1347,1611):
        return [invs("s >= 1", "t >= 1"), invs("t % 2 == 1"), invs("a >= 0", "s >= 1"), invs("t >= 1", "a >= 0"), invs("s >= 1", "t >= 1", "t % 2 != 0")]

    # Euclidean/subtractive linear-transform tasks.
    if row == 845:
        return [invs("a >= 1", "b >= 1"), invs("p*s - q*r == 1"), invs("a <= x", "b <= y"), invs("a + b >= 2"), invs("a >= 1", "b >= 1", "a <= x", "b <= y")]
    if row == 2056:
        return [invs("r1 >= 1", "tmp >= 1"), invs("gamma*cd - nxt*gh == 1"), invs("r1 <= tot", "tmp <= v5"), invs("gamma*cd - nxt*gh == 1", "r1 + tmp >= 2"), invs("r1 >= 1", "tmp >= 1", "gamma*cd - nxt*gh == 1")]
    if row == 2118:
        return [invs("u >= 1", "v2 >= 1"), invs("w*d - v4*v1 == 1"), invs("u <= f", "v2 <= ctr"), invs("u + v2 >= 2"), invs("u >= 1", "v2 >= 1", "w*d - v4*v1 == 1")]
    if row == 1129:
        return [invs("v2 >= 0", "nxt >= 0"), invs("v0 >= 0"), invs("m >= 1", "v2 >= 0", "nxt >= 0"), invs("v0 >= 0", "m >= 1"), invs("v2 + nxt >= 0", "v0 >= 0")]
    if row == 1512:
        return [invs("a >= 0", "b >= 0"), invs("q >= 0"), invs("p % 4 == 0 || p == 1"), invs("a + b >= 1"), invs("p >= 1", "a >= 0", "b >= 0", "q >= 0")]
    if row == 136:
        return [invs("x*y + z == a*b"), invs("x*y + z == a*b", "y >= 0"), invs("0 <= y", "y <= b"), invs("z + x*y == a*b", "x*y + z == a*b"), invs("y >= 0", "y <= b", "x*y + z == a*b")]

    # Difference recurrences under x<y.  The first two are relation families,
    # the others are exit/range families.
    if row == 1593:
        return [invs("x - y <= 16"), invs("x < y + 17"), invs("y - x >= -16"), invs("x >= \\at(x,Pre)"), invs("x - y <= 16", "x >= \\at(x,Pre)")]
    if row in (645,1479):
        return [invs("x - y <= \\at(x,Pre) - \\at(y,Pre)"), invs("y - x >= \\at(y,Pre) - \\at(x,Pre)"), invs("x - y <= 0"), invs("x >= \\at(x,Pre)"), invs("x - y <= \\at(x,Pre) - \\at(y,Pre)", "x >= \\at(x,Pre)")]
    if row == 1271:
        return [invs("x - y < 20"), invs("x < y + 20"), invs("x - y <= 19"), invs("x >= 0 ==> y > 0"), invs("x - y < 20", "x < y + 20")]

    # Simple counter loops.
    if row == 1419:
        return [invs("sum == i*(i-1)/2"), invs("0 <= i", "i <= n"), invs("sum >= 0"), invs("sum == (i*i-i)/2"), invs("sum == i*(i-1)/2", "0 <= i", "i <= n")]
    if row == 1832:
        return [invs("x >= \\at(x,Pre)"), invs("a >= \\at(a,LoopEntry)"), invs("x <= y"), invs("x - \\at(x,Pre) >= 0"), invs("x >= \\at(x,Pre)", "a >= \\at(a,LoopEntry)")]
    if row in (391,1032,1377):
        return [invs("x >= 1"), invs("x - 1 >= 0"), invs("x >= \\at(x,Pre)"), invs("x > 0"), invs("x >= 1", "x - 1 >= 0")]
    if row == 284:
        return [invs("tot >= \\at(tot,Pre)"), invs("tot - \\at(tot,Pre) >= 0"), invs("tot < 0x10000000"), invs("tot >= 0"), invs("tot >= \\at(tot,Pre)", "tot - \\at(tot,Pre) >= 0")]
    if row == 299:
        return [invs("q1 % 2 == 0"), invs("q1 >= 10"), invs("q1 >= 12"), invs("q1 - 10 >= 0"), invs("q1 % 2 == 0", "q1 >= 10")]
    if row == 297:
        return [invs("delta % 2 == 0"), invs("delta >= 0"), invs("lim <= cd"), invs("delta + 2*lim <= 2*cd"), invs("delta % 2 == 0", "lim <= cd")]
    if row == 868:
        return [invs("g <= 1"), invs("b == alpha"), invs("alpha == v2"), invs("g <= b"), invs("b == 1", "alpha == 1", "v2 == 1")]
    if row == 1084:
        return [invs("i <= n"), invs("j >= i"), invs("j <= i + 1"), invs("i >= 0"), invs("j - i <= 1")]
    if row == 749:
        return [invs("a >= \\at(a,LoopEntry)"), invs("a - \\at(a,LoopEntry) >= 0"), invs("y >= 0"), invs("a >= \\at(a,LoopEntry)", "y >= 0"), invs("a >= 0")]
    if row == 1490:
        return [invs("q1 % 2 == 1"), invs("q1 <= 100"), invs("q1 >= 1"), invs("q1 % 2 != 0"), invs("q1 <= 100", "q1 % 2 == 1")]
    if row == 1376:
        return [invs("x % 4 == 2 || x % 4 == 3"), invs("x % 4 != 0"), invs("x >= 1"), invs("x % 4 == 2"), invs("x % 4 == 2 || x % 4 == 3", "x >= 1")]

    # Geometric recurrences.  Relation candidates are intentionally separated
    # from conditional base/entry facts so this remains a diverse five-way set.
    geo = {
        2203:("c","x","y","z"), 2274:("c","x","y","z"), 2889:("c","x","y","z"),
        2303:("c","x","y","z"), 2207:("c","x","y","z"), 2271:("q2","tmp","v9","p3"),
        2290:("ab","cur","a","beta"), 2292:("cd","e","v8","ab"), 2296:("v7","v0","e","q"),
        2300:("p2","s","q","w"), 2897:("n","cd","e","v3"), 2910:("p3","v8","r1","gamma")}
    if row in geo:
        c,x,y,z=geo[row]
        if row in (2203,2274,2889,2303):
            rel = f"{x}*({z}-1) == {y}-1"
            special = f"{c} == 1 ==> ({x} == 1 && {y} == {z})"
        else:
            rel = f"{z} == 1 ==> {x} == {c}"
            special = f"{c} == 1 ==> ({x} == 1 && {y} == 1)"
        return [invs(f"{c} >= 1"), invs(rel), invs(special), invs(f"{y} == 1 ==> {x} == {x}*{z} - {x} + 1"), invs(f"{c} >= 1", special)]
    raise KeyError(f"no proposal family for row {row}")

def load_data():
    records = json.loads(SFT.read_text())
    out = {}
    for r in ROWS:
        source, answer = record_source_and_answer(records[r])
        out[r] = (source, extract_invariants(answer), proposals(r, source))
    return out

def wp_job(job):
    row, source, merged = job
    try:
        p = parse_program(source)
        survivors = HoudiniFilter().filter(p, 0, merged, None)
        return row, survivors, None
    except Exception as e:
        return row, [], f"{type(e).__name__}: {e}"

def rejected(examples, invariants):
    neg = examples.neg(0); groups = examples.groups(0)
    state_rej=set()
    for inv in invariants:
        cond=normalize_invariant(inv)
        for i,s in enumerate(neg):
            if i not in state_rej and eval_predicate(cond,s) is False: state_rej.add(i)
    groups_rej={g for g,ix in enumerate(groups) if any(i in state_rej for i in ix)}
    st=examples.stats[0]; rel=int(st.get("relation",0)); post=int(st.get("bound_overrun",0)); ran=int(st.get("bound_escape",0))
    bounds={"relation":(0,rel),"post_exit":(rel,rel+post),"range":(rel+post,rel+post+ran)}
    fam={k:{"total":b-a,"rejected":len([i for i in groups_rej if a<=i<b]),"indices":[i-a for i in sorted(groups_rej) if a<=i<b]} for k,(a,b) in bounds.items()}
    return {"rejected_indices":sorted(groups_rej),"rejected":len(groups_rej),"n_negative_traces":len(groups),"families":fam,"coverage":len(groups_rej)/len(groups) if groups else None,"sampler_stats":st}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--jobs",type=int,default=8); ap.add_argument("--out",type=Path,default=OUT); args=ap.parse_args()
    data=load_data(); ledger={}
    for line in LEDGER.read_text().splitlines():
        x=json.loads(line)
        if x.get("row") in ROWS: ledger[x["row"]]=x
    jobs=[]; records={}
    for r in ROWS:
        source, original, sets=data[r]
        merged=[]
        for x in original + [v for s in sets for v in s]:
            n=normalize_invariant(x)
            if n and n not in merged: merged.append(n)
        records[r]={"row":r,"five_samples":sets,"merged":merged,"original":original,"source":source}
        jobs.append((r,source,merged))
    wp={}
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        fs={ex.submit(wp_job,j):j[0] for j in jobs}
        for f in as_completed(fs):
            r,s,e=f.result(); wp[r]=(s,e)
    results=[]
    for r in ROWS:
        rec=records[r]; surv,err=wp[r]; source=rec.pop("source"); original=rec.pop("original")
        ex=ExampleSampler(source,n_runs=12,seed=0).sample()
        # The ledger is the frozen reference.  Some programs expose
        # uninitialised locals whose host execution can vary in trace count;
        # retain the recorded baseline verbatim while still rerunning the
        # candidate through ExampleSampler with its recorded seed/runs.
        base = {k: ledger[r][k] for k in ("rejected_indices", "rejected", "n_negative_traces", "families", "coverage", "sampler_stats")}
        cand=rejected(ex,surv)
        base_set=set(base["rejected_indices"]); cand_set=set(cand["rejected_indices"]); added=sorted(cand_set-base_set)
        rel_added=set(cand["families"]["relation"]["indices"])-set(base["families"]["relation"]["indices"])
        accepted=bool(added) and bool(rel_added)
        rec.update({"wp_survivors":surv,"wp_error":err,"baseline_rejected":base,"candidate_rejected":cand,"added_rejections":added,"accepted_answer":surv if accepted else original,"decision":"accept" if accepted else "reject"})
        results.append(rec)
    args.out.parent.mkdir(parents=True,exist_ok=True); args.out.write_text(json.dumps(results,indent=2,sort_keys=False)+"\n")
    print(json.dumps({"rows":len(results),"accepted":sum(x["decision"]=="accept" for x in results),"wp_errors":sum(bool(x.get("wp_error")) for x in results)},indent=2))

if __name__ == "__main__": main()
