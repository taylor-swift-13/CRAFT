"""Replay saved obligations and identify goals supplied by context alone."""
import concurrent.futures, hashlib, json, shutil, time
from collections import Counter
from pathlib import Path
import z3
import compare_exit_reuse_goals as run
from compare_exit_reuse_goals import OUT, ROOTS, W, ExitAnalysis, solve, conj, disj, boolean, digest, write

def baseline(task):
    key,goals=task
    source=(ROOTS['hidden']/'sources'/f'{key}.original.c').read_text()
    a=ExitAnalysis(source,{'survivors':[]});rows=[]
    for j,g in enumerate(goals,1):
        bad=disj([z3.And(p,z3.Not(boolean(a.logic(g,ev)))) for p,ev in a.exits])
        q=solve(z3.And(a.domain,bad),OUT/'context_queries'/key/f'goal_{j:02}.smt2')
        rows.append({'key':key,'id':j,'goal':g,'context_alone':q['status']=='unsat','proof':q})
    return rows

def replay(task):
    path,expected,sha=task
    p=OUT/path;assert digest(p)==sha
    s=z3.Solver();s.set(timeout=5000,random_seed=0);s.from_file(str(p));status=str(s.check())
    return {'smt2':path,'expected':expected,'actual':status,'match':status==expected}

def main():
    protocol=json.loads((OUT/'protocol.json').read_text());cases=[json.loads(p.read_text()) for p in sorted((OUT/'cases').glob('*.json'))]
    tasks=[]
    for r in cases:
        proofs=[r['nonvacuity']]+[g['proof'] for g in r['goals']]+[g['proof'] for g in r['unsupported_clause_redundancy']]
        for p in proofs:tasks.append((p['smt2'],p['status'],p['sha256']))
    with concurrent.futures.ProcessPoolExecutor(max_workers=8) as pool:
        replayed=list(pool.map(replay,tasks));baselines=sum(pool.map(baseline,protocol['goals'].items()),[])
    assert all(x['match'] for x in replayed)
    bmap={(r['key'],r['id']):r for r in baselines}
    assert all(b['proof']['status'] in ['sat','unsat'] for b in baselines)
    counts={arm:Counter() for arm in ROOTS}
    for r in cases:
        for g in r['goals']:
            counts[r['arm']][('context_only' if bmap[r['key'],g['id']]['context_alone'] else 'requires_invariant')+'_'+g['classification']]+=1
    write(OUT/'validation.json',{'obligations_replayed':len(replayed),'all_match':True,'checks':replayed})
    write(OUT/'context_baseline.json',{'totals':{k:dict(v) for k,v in counts.items()},'goals':baselines})
    copied=[]
    for r in cases:
        for kind in ['source','verification']:
            src=Path(r[kind]);dst=OUT/'frozen_inputs'/r['key']/r['arm']/('source.c' if kind=='source' else 'verification.json')
            assert digest(src)==r[kind+'_sha256'];dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
            copied.append({'source':str(src),'destination':str(dst.relative_to(OUT)),'sha256':digest(dst)})
    write(OUT/'input_provenance.json',{'files':copied,'original_files_unchanged':True})
    print(json.dumps({'replays':len(replayed),'counts':{k:dict(v) for k,v in counts.items()}},indent=2))

if __name__=='__main__':main()
