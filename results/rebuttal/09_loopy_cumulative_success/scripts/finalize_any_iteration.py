"""Audit completed cumulative counts and prepare/update the rebuttal answer."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import time

ROOT=Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20')
OUT=ROOT/'outputs/loopy_any_iteration_20261110'
RUN=Path('/home/yangfp/craft_empirical_runs/loopy_any_iteration_20261110')


def save(name,value):
    path=OUT/name;temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n');temp.replace(path)


def section(text):
    reviewer=re.search(r'(?ms)^## Reviewer 2[^\n]*\n.*?(?=^## Reviewer |\Z)',text)
    question=re.search(r'(?ms)^### Q1 — .*?(?=^### Q\d|\Z)',reviewer.group())
    start=reviewer.start()+question.start();end=reviewer.start()+question.end()
    return start,end,text[start:end]


def audit(result):
    points=json.loads((OUT/'proof_references.json').read_text())
    lookup={(p['visibility'],p['strategy'],p['case'],p['step']):p for p in points}
    cases=sorted({p['case'] for p in points},key=int)
    assert len(cases)==466
    for group in result['groups']:
        total=0
        for case in cases:
            prefix=[lookup[(group['visibility'],group['strategy'],case,k)] for k in range(1,group['budget']+1)]
            success=any(p.get('verified') is True for p in prefix)
            if not success:
                assert all(p.get('verified') is False for p in prefix)
            total+=success
        assert total==group['success'] and group['unresolved']==0
    checks=[]
    for path in sorted((RUN/'proofs').glob('*.json')):
        record=json.loads(path.read_text())
        if record['status']!='complete' or not record.get('verified'):
            continue
        with gzip.open(record['log'],'rt') as handle:
            events=[json.loads(line) for line in handle]
        event=events[-1]
        stdout=event.get('stdout') or ''
        assert event['returncode']==0 and '-wp' in event['args'] and '-wp-print' in event['args']
        assert event['args'][event['args'].index('-wp-timeout')+1]=='5'
        assert event['args'][event['args'].index('-wp-par')+1]=='2'
        counts=re.findall(r'Proved goals:\s*(\d+)\s*/\s*(\d+)',stdout)
        assert counts and int(counts[-1][0])==int(counts[-1][1])>0
        goals=re.split(r'(?m)^Goal ',stdout)[1:]
        cfg=re.findall(r'(?m)^\[wp\] \[CFG\] Goal (.+?) : Valid \(Unreachable\)\s*$',stdout)
        assert len(goals)+len(cfg)==int(counts[-1][1])
        assert all(re.search(r'Prover [^\n]* returns Valid\b',goal) for goal in goals)
        targets=[g for g in goals if g.startswith(('Assertion ','Post-condition ','Postcondition '))]
        cfg_targets=[g for g in cfg if re.search(r'(?:^|_)(?:assert|ensures|post)(?:_|$)',g)]
        assert targets or cfg_targets
        assert hashlib.sha256(event['input_source'].encode()).hexdigest()==record['final_source_sha256']
        checks.append(dict(record=path.name,goals=len(goals)+len(cfg),target_goals=len(targets)+len(cfg_targets)))
    assert result['changed_frozen_inputs']==[] and result['new_model_calls']==0
    output=dict(status='pass',all_cumulative_counts_reconstructed=True,new_positive_proof_logs=len(checks),
                target_goals=sum(c['target_goals'] for c in checks),proof_checks=checks)
    save('audit.json',output)
    return output


def draft(result,heading):
    groups={(g['visibility'],g['strategy'],g['budget']):g for g in result['groups']}
    def g(v,s,n):return groups[(v,s,n)]
    def rate(v,s,n):return f"{100*g(v,s,n)['rate']:.2f}%"
    lines=[heading,'',
        'Thank you for suggesting this comparison. We compare composition and vanilla iterative repair on all 466 Loopy programs using GPT-5-nano/minimal, the same parser and verifier settings, and Houdini filtering. Within each target-visibility setting, both methods share the initial response and use exactly n model calls, one response per call, for n = 1, 4, 8.','',
        'Composition pools independent responses; repair makes sequential revisions using the previous candidate and full raw verifier feedback. Both use the same initial prompt and all n calls. Visible targets are supplied to both methods and included in repair feedback; hidden targets are restored only for evaluation. Houdini evaluation results never enter the saved refinement trajectories.','',
        'We report cumulative success: a program is solved at budget n if any iteration from 1 through n succeeds, rather than only the final iteration. Each repair answer is evaluated independently after Houdini; composition is evaluated on the pooled responses available at each iteration. Thus, @4 covers every iteration from 1 to 4, and @8 covers every iteration from 1 to 8. These counts use the existing responses, with missing intermediate proofs checked locally and no additional model calls.','',
        '| Setting | Strategy | @1 | @4 | @8 | Tokens/program @8 |',
        '|---|---|---:|---:|---:|---:|']
    for v in ['hidden','visible']:
        for s in ['compose','refine']:
            name='Composition' if s=='compose' else 'Iterative repair'
            lines.append(f"| {v.title()} | {name} | {rate(v,s,1)} | {rate(v,s,4)} | {rate(v,s,8)} | {g(v,s,8)['mean_tokens']:,.0f} |")
    hidden=(g('hidden','compose',8)['rate']-g('hidden','refine',8)['rate'])*100
    visible=(g('visible','compose',8)['rate']-g('visible','refine',8)['rate'])*100
    hr=g('hidden','refine',8)['mean_tokens']/g('hidden','compose',8)['mean_tokens']
    vr=g('visible','refine',8)['mean_tokens']/g('visible','compose',8)['mean_tokens']
    lines+=['',f'At eight calls, the composition-minus-repair verification differences are {hidden:.2f} percentage points with hidden targets and {visible:.2f} points with visible targets. Repair consumes approximately {hr:.1f} and {vr:.1f} times as many tokens, respectively.','',
        'We match model-call counts and report actual token usage. Costs include all first n recorded calls, without assuming early stopping after success. The cumulative metric retains earlier successes even if later repair steps fail.','']
    body='\n'.join(lines)
    assert len(body)<5000
    return body


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--watch',action='store_true');args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    md=ROOT/'rebuttal.md'
    _,_,expected=section(md.read_text())
    save('finalizer_status.json',dict(state='waiting_for_cumulative_counts'))
    while not (OUT/'result.json').exists():
        if not args.watch:return 2
        time.sleep(10)
    result=json.loads((OUT/'result.json').read_text())
    if result['status']!='complete':
        save('finalizer_status.json',dict(state='unresolved_local_proofs',unresolved=result['unresolved_total']))
        return 2
    try:
        audit(result)
        response=draft(result,expected.splitlines()[0])
        (OUT/'reviewer2_Q1_updated.md').write_text(response)
        current=md.read_text();start,end,current_section=section(current)
        if current_section!=expected:
            save('finalizer_status.json',dict(state='draft_ready_main_section_changed',characters=len(response)))
            return 0
        updated=current[:start]+response+'\n'+current[end:]
        temp=md.with_suffix('.md.cumulative.tmp');temp.write_text(updated);temp.replace(md)
        export=ROOT/'outputs/rebuttal_comments/reviewer2_Q1.md';export.write_text(response)
        from export_rebuttal_comments import export as export_all_comments
        export_all_comments()
        save('finalizer_status.json',dict(state='complete',characters=len(response),updated_markdown=str(md)))
        print(json.dumps(dict(state='complete',groups=result['groups'])),flush=True)
        return 0
    except Exception as error:
        save('finalizer_status.json',dict(state='audit_error',error=f'{type(error).__name__}: {error}'))
        raise


if __name__=='__main__':raise SystemExit(main())
