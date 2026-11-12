"""Check provenance, replay saved SMT, and preserve every frozen input."""
import concurrent.futures, hashlib, json, shutil, time
from collections import Counter
from pathlib import Path
from compare_exit_reuse_goals import W
import z3

OUT=W/'outputs/common_success_exit_reuse_final_20261112'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def replay(path):
    records=json.loads(path.read_text());results=[]
    for name,q in records.items():
        if 'smt2' not in q:continue
        assert hashlib.sha256(q['smt2'].encode()).hexdigest()==q['sha256']
        s=z3.Solver();s.set(timeout=5000,random_seed=0);s.from_string(q['smt2']);t=time.monotonic();actual=str(s.check())
        results.append({'query':name,'expected':q['status'],'actual':actual,'seconds':time.monotonic()-t,
                        'match':actual==q['status'],'contradiction':{actual,q['status']}=={'sat','unsat'}})
    return {'key':path.stem,'checks':results}
def main():
    inv=json.loads((OUT/'inventory.json').read_text());cases=[json.loads(p.read_text()) for p in sorted((OUT/'cases').glob('*.json'))]
    assert len(cases)==578 and {r['key'] for r in cases}=={r['key'] for r in inv['programs']}
    copied=[]
    for r in cases:
        qs=json.loads((OUT/'proof_queries'/f"{r['key']}.json").read_text())
        assert len(r['selected_goals'])+r['unfilled_slots']==8
        assert sha(OUT/'candidate_pools'/f"{r['key']}.json")==r['candidate_pool_sha256']
        for g in r['selected_goals']:
            assert any(a['classification']=='supported' for a in g['arms'].values())
            assert qs[g['context_query']]['status']==('unsat' if g['exit_context_alone'] else 'sat')
            for a in g['arms'].values():
                if a['classification']=='supported':assert qs[a['query']]['status']=='unsat'
                if a['classification']=='not_entailed':assert qs[a['query']]['status']=='sat'
        for arm,meta in r['inputs'].items():
            for kind in ['source','verification','audit']:
                src=Path(meta[kind+'_path']);assert sha(src)==meta[kind+'_sha256']
                dst=OUT/'frozen_inputs'/r['key']/arm/('source.c' if kind=='source' else kind+'.json')
                dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
                assert sha(dst)==meta[kind+'_sha256']
                copied.append({'source':str(src),'destination':str(dst.relative_to(OUT)),'bytes':dst.stat().st_size,'sha256':sha(dst)})
    write(OUT/'input_provenance.json',{'files':copied,'original_inputs_unchanged':True})
    print('INPUTS',len(copied),'verified and preserved',flush=True)
    t=time.monotonic();results=[]
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(replay,p) for p in sorted((OUT/'proof_queries').glob('*.json'))]
        for f in concurrent.futures.as_completed(futures):
            r=f.result();results.append(r)
            write(OUT/'replay'/f"{r['key']}.json",r)
            if len(results)%100==0:print('REPLAY',len(results),'/ 578',flush=True)
    flat=[c for r in results for c in r['checks']];contradictions=[(r['key'],c) for r in results for c in r['checks'] if c['contradiction']]
    result={'queries':len(flat),'matches':sum(c['match'] for c in flat),'status_transitions':dict(Counter(c['expected']+' -> '+c['actual'] for c in flat)),
            'contradictions':contradictions,'seconds':time.monotonic()-t,'program_count':len(cases),'preserved_input_files':len(copied)}
    write(OUT/'validation.json',result);print(json.dumps(result,indent=2),flush=True)
    assert not contradictions

if __name__=='__main__':main()
