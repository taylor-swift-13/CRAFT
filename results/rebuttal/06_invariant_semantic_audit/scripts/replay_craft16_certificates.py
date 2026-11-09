import concurrent.futures,json,sys,time
from pathlib import Path
import z3

def one(task):
    key,queries,expected=task;results={}
    for label,status in expected.items():
        s=z3.Solver();s.set(timeout=8000);s.add(z3.parse_smt2_string(queries[label]));actual=str(s.check())
        results[label]={'expected':status,'actual':actual}
    return {'key':key,'queries':results,'pass':all(v['expected']==v['actual'] for v in results.values())}

root=Path(sys.argv[1]);tasks=[]
for p in sorted((root/'cases').glob('*.json')):
    r=json.loads(p.read_text());qfile=root/'proof_queries'/p.name
    if not qfile.exists():continue
    expected={};st=r.get('strength',{}).get('classification');ex=r.get('exact',{}).get('classification')
    if st in ['strictly_stronger','target_equivalent']:
        expected.update(strength_nonvacuity='sat',strength_forward='unsat',strength_reverse='sat' if st=='strictly_stronger' else 'unsat')
    if ex in ['proven_exact','proven_not_exact']:
        expected.update(initialization='unsat',preservation='unsat')
        if ex=='proven_exact':expected['exact_completeness']='unsat'
        else:expected.update(lemma_initialization='unsat',lemma_preservation='unsat',lemma_unreachable_state='sat')
    if expected:tasks.append((r['key'],json.loads(qfile.read_text()),expected))
records=[]
with concurrent.futures.ProcessPoolExecutor(max_workers=12) as pool:
    for row in pool.map(one,tasks):records.append(row)
out={'programs':len(records),'queries':sum(len(r['queries']) for r in records),'passed':sum(r['pass'] for r in records),'mismatches':[r for r in records if not r['pass']],'records':records}
(root/'certificate_replay.json').write_text(json.dumps(out,indent=2))
print(json.dumps({k:v for k,v in out.items() if k!='records'}))
