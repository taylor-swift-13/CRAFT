"""Investigate audit positives outside original success set without changing inputs."""
import concurrent.futures,gzip,hashlib,importlib.util,json,re,subprocess,time
from pathlib import Path
import sys
ROOT=Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20')
AUDIT=ROOT/'outputs/craft16_invariant_audit_20261109'
RUN=Path('/tmp/craft16_minimal_20261107_v2')
OUT=ROOT/'outputs/semantic_original_discrepancy_audit'
OUT.mkdir(exist_ok=True)
sys.path.insert(0,str(ROOT/'work'))
from analyze_craft16_semantics import Analysis
from rl_pipeline.common.program import parse_program
from rl_pipeline.reward import annotate
import z3
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
rows=[read(p) for p in (AUDIT/'cases').glob('*.json')]
strict={r['key'] for r in rows if not r['original_verified'] and r.get('strength',{}).get('classification')=='strictly_stronger'}
exact={r['key'] for r in rows if not r['original_verified'] and r.get('exact',{}).get('classification')=='proven_exact'}
assert len(strict)==7 and len(exact)==8
replay={r['key']:r for r in read(AUDIT/'certificate_replay.json')['records']}
def summarize_call(c):
    stdout=c.get('stdout') or ''
    goals=re.split(r'(?m)^Goal ',stdout)[1:]
    failed=[g for g in goals if not re.search(r'Prover [^\n]* returns Valid\b',g)]
    return dict(returncode=c['returncode'],summary=re.findall(r'Proved goals:[^\n]*',stdout),
        failed=[dict(header=g.splitlines()[0],prove=(re.findall(r'(?ms)^Prove: (.*?)(?=^Prover )',g) or [''])[0].strip(),
          statuses=re.findall(r'(?m)^Prover .+ returns .+',g)) for g in failed],
        frame_warnings=[l for l in stdout.splitlines() if 'Missing assigns clause' in l or 'assigns\' specification' in l])
records=[];jobs=[]
for key in sorted(strict|exact):
    case=read(AUDIT/'cases'/(key+'.json'))
    vp=RUN/'verification'/(key+'.json');sp=RUN/'sources'/(key+'.original.c')
    assert sha(vp)==case['verification_sha256'] and sha(sp)==case['source_sha256']
    original=read(vp)
    with gzip.open(RUN/'logs'/(key+'.jsonl.gz'),'rt') as h:calls=[json.loads(l) for l in h]
    final=calls[-1]
    (OUT/(key+'.original_wp.txt')).write_text((final.get('stdout') or '')+'\n'+(final.get('stderr') or ''))
    a=Analysis(sp.read_text(),original)
    record=dict(key=key,in_strict_group=key in strict,in_exact_group=key in exact,
        original_verified=original['verified'],strength=case['strength']['classification'],exact=case['exact']['classification'],
        original_wp=summarize_call(final),frame_variables=sorted(set(a.types)-a.modified),
        original_source=str(sp),original_verification=str(vp),original_log=str(RUN/'logs'/(key+'.jsonl.gz')),
        source_sha256=sha(sp),verification_sha256=sha(vp),original_log_sha256=sha(RUN/'logs'/(key+'.jsonl.gz')),
        audit_certificate_replay=replay[key])
    if key in strict:
        annotated=annotate.build_annotated(parse_program(sp.read_text()),original['survivors'],0)
        assigns=', '.join(sorted(a.modified)) or '\\nothing'
        assert 'loop invariant' in annotated
        framed=annotated.replace('loop invariant','loop assigns '+assigns+';\nloop invariant',1)
        record['diagnostic_loop_assigns']=assigns
        for mode,text in [('baseline',annotated),('explicit_loop_assigns',framed)]:
            path=OUT/(key+'.'+mode+'.c');path.write_text(text)
            cmd=final['args'][:-1]+[str(path)]
            jobs.append((key,mode,cmd))
    else:
        values={'i':0,'n':0,'b':0} if key!='linear_248' else dict(idBitLength=0,material_length=0,nlen=0,j=0,k=0)
        pre={'i':-1,'n':0} if key!='linear_248' else dict(idBitLength=0,material_length=0,nlen=0)
        bad=z3.Or(*[z3.And(p,z3.Not(q)) for p,q,_ in a.targets])
        formula=z3.And(a.domain,a.inv,bad,*[a.cur[n]==v for n,v in values.items()],*[a.entry[n]==v for n,v in values.items()],*[a.pre[n]==v for n,v in pre.items()])
        solver=z3.Solver();solver.set(timeout=8000);solver.add(formula);status=str(solver.check())
        assert status=='sat',(key,status)
        (OUT/(key+'.counterexample.smt2')).write_text(solver.to_smt2())
        record['concrete_target_counterexample']=dict(pre=pre,loop_entry=values,loop_iterations=0,status=status,
            reason='Original target is false on this legal zero-iteration execution; exact loop-head reachability does not imply target truth.')
    records.append(record)
def run(job):
    key,mode,cmd=job
    start=time.monotonic()
    try:
        result=subprocess.run(cmd,cwd=OUT,text=True,capture_output=True,timeout=75)
        call=dict(returncode=result.returncode,stdout=result.stdout,stderr=result.stderr)
        (OUT/(key+'.'+mode+'.wp.txt')).write_text(result.stdout+'\n'+result.stderr)
        return key,mode,dict(seconds=time.monotonic()-start,command=cmd,**summarize_call(call))
    except subprocess.TimeoutExpired:
        return key,mode,dict(error='75-second diagnostic process timeout',command=cmd)
bykey={r['key']:r for r in records}
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    for key,mode,result in pool.map(run,jobs):
        bykey[key].setdefault('local_wp_diagnostics',{})[mode]=result
        print(json.dumps(dict(event='diagnostic',key=key,mode=mode,summary=result.get('summary'),failed=len(result.get('failed',[])),error=result.get('error'))),flush=True)
for r in records:
    assert sha(Path(r['original_source']))==r['source_sha256']
    assert sha(Path(r['original_verification']))==r['verification_sha256']
    assert sha(Path(r['original_log']))==r['original_log_sha256']
summary=dict(strict_group=sorted(strict),exact_group=sorted(exact),overlap=sorted(strict&exact),
    unique_programs=len(records),new_model_calls=0,training_runs=0,original_inputs_changed=False,
    scope='Read-only original logs and audit certificates. Fresh WP baseline/frame diagnostics use isolated C copies; counterexamples use the audit semantics.',
    records=records)
(OUT/'findings.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(dict(event='finished',programs=len(records),original_inputs_changed=False)),flush=True)
