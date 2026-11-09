"""Read-only independent accounting audit and final export for the offline comparison."""
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys

RUN=Path('/home/yangfp/craft_empirical_runs/bestof_compose_loopy466_20261109')
EXPORT=Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20/outputs/bestof_compose_loopy466_20261109')
REPO=Path('/home/yangfp/CRAFT')
sys.dont_write_bytecode=True
sys.path.insert(0,str(REPO))
from rl_pipeline.common.program import bind_integer_constants,parse_program,state_external_integer_constants
from rl_pipeline.common.state import State,dedup_normalized,eval_predicate,extract_invariants,normalize_invariant


def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def ck(v):return digest([normalize_invariant(c) for c in v])
def save(name,v):(EXPORT/name).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def main():
    protocol=read(RUN/'protocol.json')
    assert protocol['expected']==466 and protocol['new_model_calls']==0 and protocol['training_runs']==0
    assert protocol['selection_sees_targets'] is False
    issues=[];programs=[];candidate_rows=[];rows=[];witness=[];coverage_rechecks=[]
    for key,case in protocol['cases'].items():
        rp=RUN/'per_program'/(key+'.json')
        if not rp.exists():continue
        program=read(rp);programs.append(program)
        gen=read(case['generation_path'])
        assert gen['reasoning_effort']=='minimal' if 'reasoning_effort' in gen else True
        raw=[dedup_normalized(extract_invariants(t,max_invariants=20)) for t in gen['responses']]
        assert len(raw)==16 and [ck(r) for r in raw]==case['candidate_keys']
        sample=read(RUN/'samples'/(key+'.json'))
        assert sample['status']=='complete'
        groups=sample['negative_groups'];states=[State(**s) for s in sample['negative_states']]
        assert sample['scorable']==bool(groups)
        assert all(g and all(0<=i<len(states) for i in g) for g in groups)
        candidates=[]
        for i,set_key in enumerate(case['candidate_keys']):
            record=read(RUN/'single_proofs'/f'{key}_{set_key}.json')
            assert record['status']=='complete' and record['generation_sha256']==case['generation_sha256']
            assert record['clauses']==raw[i]
            assert isinstance(record['verified'],bool)
            candidates.append(record)
            candidate_rows.append(dict(key=key,index=i+1,verified=record['verified'],
                raw_clauses=len(raw[i]),survivor_clauses=len(record['survivors']),
                rejected=program['selection']['rejection_counts'][i],negative_groups=len(groups),
                reused=record.get('reused',False),recorded_new_seconds=record['seconds']))
        counts=program['selection']['rejection_counts']
        assert len(counts)==16 and all(0<=c<=len(groups) for c in counts)
        for row in program['rows']:
            n=row['n'];chosen=max(range(n),key=lambda i:(counts[i],-i))
            assert row['scorable']==bool(groups)
            assert chosen+1==row['selected_response']
            assert row['best_coverage_verified']==candidates[chosen]['verified']
            assert row['oracle_best_verified']==any(c['verified'] for c in candidates[:n])
            assert row['best_rejected']==max(counts[:n])
            comp_ref=case['compose'][str(n)]
            assert sha(comp_ref['path'])==comp_ref['sha256']
            comp=read(comp_ref['path'])
            assert comp['union']==dedup_normalized(c for r in raw[:n] for c in r)
            assert row['compose_verified']==comp['verified']
            assert row['compose_survivor_clauses']==len(comp['survivors'])
            assert row['best_survivor_clauses']==len(candidates[chosen]['survivors'])
            if n==1:
                assert row['best_coverage_verified']==row['oracle_best_verified']==row['compose_verified']
                assert row['best_rejected']==row['compose_rejected']
            if groups:
                assert abs(row['best_coverage']-max(counts[:n])/len(groups))<1e-12
                assert abs(row['compose_coverage']-row['compose_rejected']/len(groups))<1e-12
            else:assert chosen==0 and row['best_coverage'] is None
            row=dict(row,max_coverage_ties=sum(c==max(counts[:n]) for c in counts[:n]),
                     best_coverage_saturated=bool(groups) and max(counts[:n])==len(groups))
            rows.append(row)
            if row['compose_verified']!=row['oracle_best_verified']:
                witness.append(dict(key=key,n=n,kind='compose_only' if row['compose_verified'] else 'oracle_only',
                    compose_verified=row['compose_verified'],oracle_best_verified=row['oracle_best_verified'],
                    best_coverage_verified=row['best_coverage_verified'],selected_response=chosen+1,
                    original_source=case['original_path'],compose_reference=comp_ref['path']))
        # Independently enumerate trace groups for a fixed, deterministic audit subset.
        if len(coverage_rechecks)<12:
            constants=state_external_integer_constants(parse_program(Path(case['hidden_path']).read_text()))
            def group_count(invariants):
                expressions=[bind_integer_constants(normalize_invariant(c),constants) for c in invariants]
                return sum(any(eval_predicate(expr,states[i]) is False for i in g for expr in expressions) for g in groups)
            assert [group_count(c['survivors']) for c in candidates]==counts
            for row in program['rows']:
                comp=read(case['compose'][str(row['n'])]['path'])
                assert group_count(comp['survivors'])==row['compose_rejected']
            coverage_rechecks.append(key)
    complete=len(programs)==466
    summary=[]
    for n in [1,4,8,16]:
        rs=[r for r in rows if r['n']==n];sc=[r for r in rs if r['scorable']]
        if not rs:continue
        summary.append(dict(n=n,programs=len(rs),expected=466,scorable=len(sc),
            best_coverage_success=sum(r['best_coverage_verified'] for r in rs),
            oracle_best_success=sum(r['oracle_best_verified'] for r in rs),
            compose_success=sum(r['compose_verified'] for r in rs),
            compose_only_vs_oracle=sum(r['compose_verified'] and not r['oracle_best_verified'] for r in rs),
            oracle_only_vs_compose=sum(r['oracle_best_verified'] and not r['compose_verified'] for r in rs),
            compose_only_vs_coverage=sum(r['compose_verified'] and not r['best_coverage_verified'] for r in rs),
            coverage_only_vs_compose=sum(r['best_coverage_verified'] and not r['compose_verified'] for r in rs),
            mean_best_coverage=sum(r['best_coverage'] for r in sc)/len(sc) if sc else None,
            mean_compose_coverage=sum(r['compose_coverage'] for r in sc)/len(sc) if sc else None,
            compose_higher_coverage=sum(r['compose_rejected']>r['best_rejected'] for r in sc),
            compose_lower_coverage=sum(r['compose_rejected']<r['best_rejected'] for r in sc),
            best_accept_half=sum(r['best_meets_half_coverage'] for r in rs),
            compose_accept_half=sum(r['compose_meets_half_coverage'] for r in rs),
            best_max_coverage_ties=sum(r['max_coverage_ties']>1 for r in rs),
            best_max_coverage_saturated=sum(r['best_coverage_saturated'] for r in rs)))
    if complete:
        report=read(EXPORT/'result.json')
        assert report['state']=='complete'
        for s in summary:
            known=report['summary'][str(s['n'])]
            for a,b in [('best_coverage','best_coverage_success'),('oracle_best','oracle_best_success'),('compose','compose_success')]:
                assert s[b]==known[a]['success']
    changed=[p for p,h in {**protocol['frozen_inputs'],**protocol['frozen_sources']}.items() if sha(p)!=h]
    assert not changed
    audit=dict(status='pass' if complete else 'partial_pass',completed_programs=len(programs),expected=466,
        source_files_checked=len(protocol['frozen_inputs'])+len(protocol['frozen_sources']),changed=changed,
        response_order_and_clause_caps_checked=True,selector_target_blind_inputs=True,
        best_coverage_selection_recomputed=True,oracle_upper_bound_recomputed=True,
        composition_prefix_unions_recomputed=True,negative_trace_group_rechecks=coverage_rechecks,
        same_n1_outputs_checked=True,driver_sha256=protocol['driver_sha256'],new_model_calls=0,training_runs=0)
    save('comparison_audit.json',audit)
    save('comparison_analysis.json',dict(state='complete' if complete else 'partial',summary=summary,
        note='No full-population rate is reported before all 466 programs finish. BestCoverage ties choose earliest; unscorable cases also choose earliest. OracleBest is target-aware evaluation only.'))
    for filename,data in [('summary.csv',summary),('candidate_details.csv',candidate_rows),('paired_witness_cases.csv',witness)]:
        if data:
            out=io.StringIO();w=csv.DictWriter(out,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
            (EXPORT/filename).write_text(out.getvalue())
    lines=['# Best-of-N versus Compose-of-N', '',f'Status: {"complete" if complete else "partial"}; {len(programs)}/466 Loopy programs.',
        '', 'Existing GPT-5-nano/minimal responses only; no training and no new model calls.', '',
        '| N | BestCoverage | OracleBest (upper bound) | Compose | Compose-only vs oracle | Oracle-only vs compose |',
        '|---|---:|---:|---:|---:|---:|']
    for s in summary:
        def cell(k):return f"{s[k]}/466 ({100*s[k]/466:.2f}%)" if complete else f"{s[k]}/{s['programs']} processed"
        lines.append(f"| {s['n']} | {cell('best_coverage_success')} | {cell('oracle_best_success')} | {cell('compose_success')} | {s['compose_only_vs_oracle']} | {s['oracle_only_vs_compose']} |")
    lines+=['','BestCoverage selects by common negative-trace coverage after independent filtering, without targets. OracleBest asks whether any independently filtered response proves the target and is not a deployable selector. Compose jointly filters the same prefix union.',
        '', 'Matched budget means the same generated responses, not equal verifier compute. These results test inference and label-construction mechanisms; they do not measure retrained SFT performance.']
    (EXPORT/'comparison_report.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(audit,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
