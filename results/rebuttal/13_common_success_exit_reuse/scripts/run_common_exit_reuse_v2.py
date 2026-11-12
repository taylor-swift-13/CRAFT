"""Symmetric source-template reuse probe on ALL originally successful pairs."""
import ast, concurrent.futures, hashlib, json, os, sys, time
from collections import Counter
from pathlib import Path
import z3
from common_exit_goal_templates import make_pool, FAMILIES, LIMIT
from compare_exit_reuse_goals import W, ROOTS, ExitAnalysis, abstract_divisions, conj, disj, boolean

OUT=W/'outputs/common_success_exit_reuse_20261112_v2'
SCREEN_MS=250
FINAL_MS=5000
GOALS=8

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def check(s,formula,ms,detail=False):
    s.set(timeout=ms,random_seed=0);s.push();s.add(formula);t=time.monotonic();r=s.check()
    out={'status':str(r),'seconds':time.monotonic()-t}
    if r==z3.unknown:out['reason']=s.reason_unknown()
    if detail:
        out['smt2']=s.to_smt2()
        out['sha256']=hashlib.sha256(out['smt2'].encode()).hexdigest()
        if r==z3.sat:out['model']={str(d):str(s.model()[d]) for d in s.model().decls()}
    s.pop();return out
def fresh(context):
    s=z3.Solver();s.add(context);return s

def run(row):
    key=row['key'];dest=OUT/'cases'/f'{key}.json'
    if dest.exists():return json.loads(dest.read_text())
    t=time.monotonic();pool=json.loads((OUT/'candidate_pools'/f'{key}.json').read_text())
    r={'key':key,'program_included':True,'planned_goal_count':GOALS,'candidate_pool_sha256':sha(OUT/'candidate_pools'/f'{key}.json'),
       'inputs':row['arms'],'selected_goals':[],'screening':[],'arms':{},'unfilled_slots':GOALS}
    if 'error' in pool:
        r['error']='candidate_source_parse: '+pool['error'];write(dest,r);return r
    try:a=ExitAnalysis(Path(row['arms']['hidden']['source_path']).read_text(),{'survivors':[]})
    except Exception as e:
        r['error']='semantic_model: '+repr(e);write(dest,r);return r
    paths=disj([pc for pc,ev in a.exits]);base=fresh(a.domain)
    contexts={};solvers={};eligible={};proofs={}
    for arm in ['hidden','visible']:
        rec=json.loads(Path(row['arms'][arm]['verification_path']).read_text());cs=[];unsupported=[]
        for c in rec['survivors']:
            try:cs.append(boolean(a.logic(c,a.cur)))
            except Exception as e:unsupported.append((c,str(e)))
        inv=conj(cs);context=conj([a.domain,inv]);s=fresh(context);redundancy=[]
        for j,(c,error) in enumerate(unsupported):
            try:
                abstract=abstract_divisions(a,c)
                proof=check(s,z3.Not(abstract),FINAL_MS,True)
            except Exception as e:proof={'status':'unsupported','reason':str(e)}
            if 'smt2' in proof:
                name=f'{arm}_redundancy_{j:02}';proofs[name]=proof;proof={'query':name,'status':proof['status']}
            redundancy.append({'clause':c,'translation_error':error,'proof':proof})
        complete=all(x['proof']['status']=='unsat' for x in redundancy)
        nv=check(s,paths,FINAL_MS,True);name=arm+'_nonvacuity';proofs[name]=nv
        eligible[arm]=complete and nv['status']=='sat'
        r['arms'][arm]={'full_summary_represented':complete,'nonvacuity':{'query':name,'status':nv['status']},
                         'unsupported_clause_redundancy':redundancy,'eligible_for_confirmation':eligible[arm]}
        contexts[arm]=context;solvers[arm]=s
    nv=check(base,paths,FINAL_MS,True);proofs['context_nonvacuity']=nv
    if not any(eligible.values()):
        r['error']='neither arm has a fully represented, confirmed feasible exit summary'
        r['seconds']=time.monotonic()-t;write(OUT/'proof_queries'/f'{key}.json',proofs);write(dest,r);return r
    families={f:[c for c in pool['candidates'] if c['family']==f] for f in FAMILIES}
    indices={f:0 for f in FAMILIES};seen_normalized=set()
    # Exclude formulas syntactically equivalent after simplification to an
    # original assertion; related or weaker properties remain explicitly allowed.
    original=set()
    for g in a.goal_text:
        try:original.add(a.logic(g,a.cur).sexpr())
        except Exception:pass
    def attempt(c):
        item={'candidate_id':c['id']};r['screening'].append(item)
        try:
            normalized=a.logic(c['formula'],a.cur).sexpr()
            if normalized in seen_normalized or normalized in original:
                item['classification']='duplicate_or_original_assertion';return False
            seen_normalized.add(normalized)
            bad=disj([z3.And(pc,z3.Not(boolean(a.logic(c['formula'],ev)))) for pc,ev in a.exits])
        except Exception as e:
            item['classification']='unsupported_goal';item['error']=str(e);return False
        statuses={arm:check(solvers[arm],bad,SCREEN_MS) if eligible[arm] else {'status':'unconfirmed_summary'} for arm in solvers}
        item['arms']=statuses
        if not any(eligible[arm] and statuses[arm]['status']=='unsat' for arm in solvers):
            item['classification']='not_confirmed_correct';return False
        cb=check(base,bad,SCREEN_MS);item['context_alone']=cb
        if cb['status'] not in ['sat','unsat']:
            item['classification']='context_unknown';return False
        # An exit condition may itself establish a meaningful postcondition.
        # Keep such targets separately, while excluding properties already
        # entailed by entry/frame/type constraints without any exit condition.
        prebad=z3.Not(boolean(a.logic(c['formula'],a.cur)))
        entry=check(base,prebad,SCREEN_MS);item['entry_alone']=entry
        if entry['status']!='sat':
            item['classification']='entry_implied' if entry['status']=='unsat' else 'entry_unknown';return False
        # Recheck every selected formula with the same 5-second budget in both arms.
        j=len(r['selected_goals'])+1;result={'slot':j,**c,'arms':{}}
        for arm in solvers:
            q=check(solvers[arm],bad,FINAL_MS,True);name=f'goal_{j:02}_{arm}';proofs[name]=q
            status=('supported' if q['status']=='unsat' else 'not_entailed' if q['status']=='sat' else 'unconfirmed_solver') if eligible[arm] else 'unconfirmed_summary'
            result['arms'][arm]={'classification':status,'query':name,'solver_status':q['status']}
        q=check(base,bad,FINAL_MS,True);name=f'goal_{j:02}_context';proofs[name]=q;result['context_query']=name
        result['exit_context_alone']=q['status']=='unsat'
        eq=check(base,prebad,FINAL_MS,True);name=f'goal_{j:02}_entry';proofs[name]=eq;result['entry_query']=name
        if q['status'] not in ['sat','unsat'] or eq['status']!='sat' or not any(v['classification']=='supported' for v in result['arms'].values()):
            item['classification']='not_confirmed_at_final_budget';return False
        r['selected_goals'].append(result);item['classification']='selected';item['slot']=j;return True
    # First pass gives each syntactic family at most one slot. A second pass
    # fills from the remaining candidate lists, always in the fixed family order.
    for f in FAMILIES:
        while indices[f]<len(families[f]):
            c=families[f][indices[f]];indices[f]+=1
            if attempt(c):break
    while len(r['selected_goals'])<GOALS:
        progressed=False
        for f in FAMILIES:
            if len(r['selected_goals'])>=GOALS:break
            if indices[f]<len(families[f]):
                c=families[f][indices[f]];indices[f]+=1;progressed=True;attempt(c)
        if not progressed:break
    r['unfilled_slots']=GOALS-len(r['selected_goals']);r['seconds']=time.monotonic()-t
    r['proof_queries_sha256']=None
    qp=OUT/'proof_queries'/f'{key}.json';write(qp,proofs);r['proof_queries_sha256']=sha(qp)
    for arm,paths in row['arms'].items():
        for kind in ['source','verification']:assert sha(Path(paths[kind+'_path']))==paths[kind+'_sha256']
    write(dest,r);return r

def summarize(results,wall):
    totals={arm:Counter() for arm in ['hidden','visible']};paired=Counter();programs=[];families=Counter()
    for r in results:
        per={arm:Counter() for arm in totals}
        for g in r['selected_goals']:
            families[g['family']]+=1
            for arm in totals:
                status=g['arms'][arm]['classification'];totals[arm][status]+=1;per[arm][status]+=1
            h=g['arms']['hidden']['classification'];v=g['arms']['visible']['classification']
            if h.startswith('unconfirmed') or v.startswith('unconfirmed'):paired['unconfirmed']+=1
            elif h==v=='supported':paired['both_supported']+=1
            elif h=='supported':paired['hidden_only']+=1
            elif v=='supported':paired['visible_only']+=1
            else:paired['neither_supported']+=1
        for arm in totals:totals[arm]['unfilled_goal_slot']+=r['unfilled_slots']
        programs.append({'key':r['key'],'selected_goals':len(r['selected_goals']),'unfilled_slots':r['unfilled_slots'],
                         'counts':{k:dict(v) for k,v in per.items()},'error':r.get('error')})
    summary={'programs_total':len(results),'planned_goal_slots':GOALS*len(results),'selected_goal_count':sum(len(r['selected_goals']) for r in results),
             'programs_with_eight_goals':sum(len(r['selected_goals'])==GOALS for r in results),
             'totals':{k:dict(v) for k,v in totals.items()},'paired':dict(paired),'families':dict(families),
             'wall_seconds':wall,'z3_version':z3.get_version_string(),'programs':programs}
    write(OUT/'summary.json',summary);return summary

def main():
    import shutil
    OUT.mkdir(parents=True,exist_ok=True)
    if not (OUT/'inventory.json').exists():
        shutil.copyfile(W/'outputs/common_success_exit_reuse_20261112/inventory.json',OUT/'inventory.json')
    inv=json.loads((OUT/'inventory.json').read_text());rows=inv['programs']
    protocol={'selection':inv['selection'],'programs':len(rows),'goals_per_program':GOALS,
      'candidate_source':'Source-only deterministic grammar; no invariant or outcome is read by make_pool.',
      'correctness_confirmation':'At least one fully represented, nonvacuous frozen summary entails the candidate; both arms are checked symmetrically.',
      'selection_order':'Fixed family order, first confirmed non-contextual goal from each family; then round-robin remaining candidates until eight. No ranking by which arm wins.',
      'context_filter':'Exclude entry/frame/type-implied properties. Keep meaningful properties established by the exit condition itself, but flag and report them separately from goals requiring invariant information.',
      'missing_goals':'Keep the program and all eight planned slots; unavailable slots are reported, never counted as supported or padded with tautologies.',
      'scope':'Immediate normal loop exit, including updates before break; original assertions are not assumed.',
      'semantics':'Existing defined 32-bit scalar-integer execution model; mathematical integer assertions.',
      'screen_timeout_ms':SCREEN_MS,'final_timeout_ms':FINAL_MS,'solver_seed':0,'workers':8,'max_candidates_per_family':LIMIT,
      'family_order':FAMILIES,'not_independent_held_out':'Goals are source-derived but correctness-curated using both summaries; properties may imply one another.',
      'model_calls':0,'training_runs':0,'Houdini_runs':0,'Frama_C_runs':0}
    protocol['scripts']={n:sha(W/'work'/n) for n in ['run_common_exit_reuse_v2.py','common_exit_goal_templates.py','compare_exit_reuse_goals.py','analyze_craft16_semantics.py']}
    pp=OUT/'protocol.json'
    if pp.exists():assert json.loads(pp.read_text())==protocol
    else:write(pp,protocol)
    # Freeze ALL candidate pools before checking the first candidate.
    for row in rows:
        p=OUT/'candidate_pools'/f"{row['key']}.json"
        if not p.exists():
            try:pool=make_pool(Path(row['arms']['hidden']['source_path']).read_text())
            except Exception as e:pool={'error':repr(e),'candidates':[]}
            write(p,pool)
    write(OUT/'candidate_manifest.json',{'files':[{'path':str(p.relative_to(OUT)),'sha256':sha(p)} for p in sorted((OUT/'candidate_pools').glob('*.json'))]})
    print('FROZEN',len(rows),'programs; starting symmetric offline checks',flush=True)
    t=time.monotonic();results=[]
    with concurrent.futures.ProcessPoolExecutor(max_workers=8) as pool:
        futures={pool.submit(run,row):row['key'] for row in rows}
        for future in concurrent.futures.as_completed(futures):
            r=future.result();results.append(r)
            if len(results)%20==0 or len(results)==len(rows):
                print('PROGRESS',len(results),'/',len(rows),'selected',sum(len(v['selected_goals']) for v in results),'elapsed',round(time.monotonic()-t,1),flush=True)
    results.sort(key=lambda r:r['key']);summary=summarize(results,time.monotonic()-t)
    print(json.dumps({k:v for k,v in summary.items() if k!='programs'},indent=2),flush=True)

if __name__=='__main__':main()
