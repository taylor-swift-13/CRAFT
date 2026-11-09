import csv,hashlib,json
from collections import Counter
from pathlib import Path

ROOT=Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20')
OUT=ROOT/'outputs/craft16_invariant_audit_20261109'
RUN=Path('/tmp/craft16_minimal_20261107_v2')
rows=[json.loads(p.read_text()) for p in sorted((OUT/'cases').glob('*.json'))]
replay=json.loads((OUT/'certificate_replay.json').read_text())
explicit=json.loads((OUT/'explicit_predecessor_certificates.json').read_text())
resolved={r['key'] for r in explicit if r['status']=='unsat' and r['independent_replay']=='unsat'}
remaining=[]
for r in replay['mismatches']:
    for label,result in r['queries'].items():
        if result['actual']!=result['expected'] and not (label=='exact_completeness' and r['key'] in resolved):remaining.append([r['key'],label,result])
assert not remaining,remaining
assert len(rows)==832
def summarize(subset):
    n=len(subset)
    return {'programs':n,'strength':dict(Counter(r.get('strength',{}).get('classification','unsupported') for r in subset)),'exact':dict(Counter(r.get('exact',{}).get('classification','unsupported') for r in subset)),'strict_and_exact':sum(r.get('strength',{}).get('classification')=='strictly_stronger' and r.get('exact',{}).get('classification')=='proven_exact' for r in subset)}
changed=[]
for r in rows:
    for field,path in [('source_sha256',RUN/'sources'/(r['key']+'.original.c')),('verification_sha256',RUN/'verification'/(r['key']+'.json'))]:
        if hashlib.sha256(path.read_bytes()).hexdigest()!=r[field]:changed.append(str(path))
assert not changed,changed
report={'all':summarize(rows),'original_verified':summarize([r for r in rows if r['original_verified']]),'certificate_audit':{'programs':replay['programs'],'ordinary_queries':replay['queries'],'quantified_replay_timeouts_resolved_by_explicit_predecessors':sorted(resolved),'remaining_disagreements':remaining},'original_integrity':{'source_files':832,'verification_files':832,'changed':changed},'analysis_driver_sha256':hashlib.sha256((ROOT/'work/analyze_craft16_semantics.py').read_bytes()).hexdigest()}
(OUT/'final_summary.json').write_text(json.dumps(report,indent=2))
with (OUT/'per_program.csv').open('w',newline='') as f:
    w=csv.writer(f);w.writerow(['key','suite','original_verified','strength','exact','reason'])
    for r in rows:w.writerow([r['key'],r['suite'],r['original_verified'],r.get('strength',{}).get('classification','unsupported'),r.get('exact',{}).get('classification','unsupported'),r.get('error','')])
print(json.dumps(report,indent=2))
