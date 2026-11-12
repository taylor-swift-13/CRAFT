"""Preserve non-contextual selections; fill shortages with exit-context goals.

No sorting or selection uses which arm wins. No additional solver calls.
"""
import copy, hashlib, json, shutil
from collections import Counter
from pathlib import Path
from compare_exit_reuse_goals import W

V1=W/'outputs/common_success_exit_reuse_20261112'
V2=W/'outputs/common_success_exit_reuse_20261112_v2'
OUT=W/'outputs/common_success_exit_reuse_final_20261112'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    for name in ['inventory.json','candidate_manifest.json']:
        assert (V1/name).read_bytes()==(V2/name).read_bytes()
        shutil.copyfile(V1/name,OUT/name)
    inv=json.loads((OUT/'inventory.json').read_text());rows=[]
    protocol={'sample':'Every program with verified=true in both original @16 runs; 578 programs. No strength/exactness selection.',
      'candidate_construction':'Unchanged source-only pools shared by both stages and both model arms.',
      'selection':'First retain all stage-1 non-contextual selections in their fixed family order. If fewer than eight, append stage-2 exit-context-only targets in their fixed order. No criterion depends on the winning arm.',
      'target_correctness':'At least one fully represented, nonvacuous frozen summary confirms the target. Both arms are evaluated against exactly the same target.',
      'context_only':'An exit-context goal is entailed by entry/frame/type/exit conditions without any invariant, but not by entry/frame/type conditions alone.',
      'missing':'All programs remain in the inventory with eight slots. Unfilled slots are explicit, not fabricated goals or automatic failures.',
      'scope':'Immediate modeled loop exit, including updates before break. No original assertion is assumed.',
      'dependence':'Correctness curation uses both summaries. This is a symmetric template probe, not independently authored held-out goals. Goals can imply each other.',
      'stages':{'non_contextual_protocol_sha256':sha(V1/'protocol.json'),'exit_context_protocol_sha256':sha(V2/'protocol.json')},
      'scope_note':'In the stage-2 protocol, the inherited words non-contextual in selection_order mean non-entry-implied; its context_filter field and implementation explicitly retain exit-context goals.',
      'generation_calls':0,'training_runs':0,'Houdini_runs':0,'Frama_C_runs':0,
      'final_query_timeout_ms':5000,'screen_timeout_ms':250,'solver_seed':0,'z3_version':'4.13.3'}
    write(OUT/'protocol.json',protocol)
    for item in inv['programs']:
        key=item['key'];r=copy.deepcopy(json.loads((V1/'cases'/f'{key}.json').read_text()));s=json.loads((V2/'cases'/f'{key}.json').read_text())
        assert sha(V1/'candidate_pools'/f'{key}.json')==sha(V2/'candidate_pools'/f'{key}.json')
        poolpath=OUT/'candidate_pools'/f'{key}.json';poolpath.parent.mkdir(exist_ok=True);shutil.copyfile(V1/'candidate_pools'/f'{key}.json',poolpath)
        proofs={};selected=[]
        for stage,raw in [('non_contextual',r),('exit_context',s)]:
            root=V1 if stage=='non_contextual' else V2;qp=root/'proof_queries'/f'{key}.json'
            oldproofs=json.loads(qp.read_text()) if qp.exists() else {}
            for name,p in oldproofs.items():
                if name.startswith('goal_'):continue
                proofs[stage+'/'+name]=p
            for goal in raw['selected_goals']:
                if len(selected)==8:break
                if stage=='exit_context' and not goal['exit_context_alone']:continue
                g=copy.deepcopy(goal);g['source_stage']=stage;g['source_slot']=g['slot'];g['slot']=len(selected)+1
                g['exit_context_alone']=stage=='exit_context'
                for arm in g['arms']:
                    old=g['arms'][arm]['query'];new=stage+'/'+old;proofs[new]=oldproofs[old];g['arms'][arm]['query']=new
                for field in ['context_query','entry_query']:
                    if field in g:
                        old=g[field];new=stage+'/'+old;proofs[new]=oldproofs[old];g[field]=new
                selected.append(g)
        # Keep supporting-stage metadata with original query names under a
        # documented namespace, so unsupported clauses remain traceable.
        result={'key':key,'program_included':True,'planned_goal_count':8,'inputs':item['arms'],
          'candidate_pool_sha256':sha(poolpath),'selected_goals':selected,'unfilled_slots':8-len(selected),
          'arm_support':{'non_contextual':r['arms'],'exit_context':s['arms']},
          'error':r.get('error') if not selected else None,'screening_stage_files':{'non_contextual':f'history/non_contextual/cases/{key}.json','exit_context':f'history/exit_context/cases/{key}.json'}}
        qp=OUT/'proof_queries'/f'{key}.json';write(qp,proofs);result['proof_queries_sha256']=sha(qp)
        write(OUT/'cases'/f'{key}.json',result);rows.append(result)
    totals={a:Counter() for a in ['hidden','visible']};paired=Counter();core=Counter();families=Counter();missing=Counter()
    complete=[]
    for r in rows:
        if r['unfilled_slots']:
            err=r.get('error')
            if err and err.startswith('semantic_model'):reason='semantic_model_unsupported'
            elif err:
                arms=r['arm_support']['non_contextual']
                reason='infeasible_exit_context' if arms and all(a['nonvacuity']['status']=='unsat' for a in arms.values()) else 'summary_translation_unconfirmed'
            else:reason='template_pool_insufficient'
            missing[reason+'_programs']+=1;missing[reason+'_slots']+=r['unfilled_slots']
        for g in r['selected_goals']:
            families[g['family']]+=1
            h=g['arms']['hidden']['classification'];v=g['arms']['visible']['classification']
            for a in totals:totals[a][g['arms'][a]['classification']]+=1
            label='unconfirmed' if h.startswith('unconfirmed') or v.startswith('unconfirmed') else 'both_supported' if h==v=='supported' else 'hidden_only' if h=='supported' else 'visible_only' if v=='supported' else 'neither_supported'
            paired[label]+=1
            if not g['exit_context_alone']:core[label]+=1
        if len(r['selected_goals'])==8:complete.append(r['key'])
    summary={'programs_total':len(rows),'planned_goal_slots':8*len(rows),'selected_goal_count':sum(len(r['selected_goals']) for r in rows),
      'programs_with_eight_goals':len(complete),'programs_with_eight_goals_keys':complete,
      'exit_context_only_goals':sum(g['exit_context_alone'] for r in rows for g in r['selected_goals']),
      'non_contextual_goals':sum(not g['exit_context_alone'] for r in rows for g in r['selected_goals']),
      'totals':{a:dict(v) for a,v in totals.items()},'paired_all':dict(paired),'paired_non_contextual':dict(core),
      'missing':dict(missing),'families':dict(families)}
    write(OUT/'summary.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if not k.endswith('_keys')},indent=2))

if __name__=='__main__':main()
