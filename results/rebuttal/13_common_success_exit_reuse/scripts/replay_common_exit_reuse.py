"""Standalone read-only replay; requires only Python and z3-solver 4.13.3."""
import argparse, concurrent.futures, hashlib, json
from collections import Counter
from pathlib import Path
import z3

def replay(path):
    out=[]
    for name,q in json.loads(path.read_text()).items():
        if 'smt2' not in q:continue
        assert hashlib.sha256(q['smt2'].encode()).hexdigest()==q['sha256']
        s=z3.Solver();s.set(timeout=5000,random_seed=0);s.from_string(q['smt2'])
        actual=str(s.check());out.append((path.stem,name,q['status'],actual))
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--workers',type=int,default=4)
    args=ap.parse_args()
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        rows=sum(pool.map(replay,sorted((args.root/'proof_queries').glob('*.json'))),[])
    contradictions=[r for r in rows if {r[2],r[3]}=={'sat','unsat'}]
    lemmas=[]
    lp=args.root/'replay_lemma_followup.json'
    if lp.exists():
        for index,q in enumerate(json.loads(lp.read_text())['obligations']):
            assert hashlib.sha256(q['smt2'].encode()).hexdigest()==q['sha256']
            s=z3.Solver() if index==0 else z3.SolverFor('QF_NRA')
            s.set(timeout=5000);s.from_string(q['smt2']);lemmas.append(str(s.check()))
    print(json.dumps({'queries':len(rows),'status_transitions':dict(Counter(r[2]+' -> '+r[3] for r in rows)),
                      'contradictions':contradictions,'nonmatching':[r for r in rows if r[2]!=r[3]],'supplemental_lemma_statuses':lemmas},indent=2))
    if contradictions:raise SystemExit(1)

if __name__=='__main__':main()
