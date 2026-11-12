"""Read-only replay of archived SMT2; no model, CRAFT, or verifier required."""
import argparse, concurrent.futures, hashlib, json
from pathlib import Path
import z3

def check(task):
    p,status,sha=task
    assert hashlib.sha256(p.read_bytes()).hexdigest()==sha,str(p)
    s=z3.Solver();s.set(timeout=5000,random_seed=0);s.from_file(str(p))
    actual=str(s.check())
    return actual==status,str(p),status,actual

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);args=ap.parse_args();root=args.root
    tasks=[]
    for f in sorted((root/'cases').glob('*.json')):
        r=json.loads(f.read_text())
        for p in [r['nonvacuity']]+[g['proof'] for g in r['goals']]+[g['proof'] for g in r['unsupported_clause_redundancy']]:
            tasks.append((root/p['smt2'],p['status'],p['sha256']))
    for r in json.loads((root/'context_baseline.json').read_text())['goals']:
        p=r['proof'];tasks.append((root/p['smt2'],p['status'],p['sha256']))
    with concurrent.futures.ProcessPoolExecutor(max_workers=8) as pool:results=list(pool.map(check,tasks))
    bad=[r for r in results if not r[0]]
    print(json.dumps({'queries':len(results),'matched':len(results)-len(bad),'mismatches':bad},indent=2))
    if bad:raise SystemExit(1)

if __name__=='__main__':main()
