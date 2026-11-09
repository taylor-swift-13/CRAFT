"""Read-only extraction/proof audit. No API, credentials, or model generation."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import ast
import gzip
import hashlib
import json
import random
import re
import sys

sys.dont_write_bytecode = True
REPO = Path('/home/yangfp/CRAFT')
RUN = Path('/tmp/craft_loopy_cached_local_verify_20261107')
OUT = Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20/outputs/loopy_local_verify')
sys.path.insert(0, str(REPO))
from rl_pipeline.common.state import extract_invariants, dedup_normalized
from rl_pipeline.common.program import parse_program
from rl_pipeline.reward.annotate import build_annotated


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def objhash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def native_functions():
    path = REPO/'experiments/gpt5nano_full832/loopy_adapter.py'
    tree = ast.parse(path.read_text())
    names = {'_official_invariants', '_candidate_rollouts'}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = dict(extract_invariants=extract_invariants, random=random, SHUFFLE_TIMES=10,
              COMBINE_K=8)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    return ns['_official_invariants'], ns['_candidate_rollouts']


def proof_blocks(stdout):
    blocks = [b.strip() for b in stdout.split('------------------------------------------------------------')]
    blocks = [b for b in blocks if b.startswith('Goal ')]
    invariants = [b for b in blocks if b.startswith(('Goal Establishment of Invariant',
                                                    'Goal Preservation of Invariant'))]
    targets = [b for b in blocks if b.startswith(('Goal Assertion','Goal Post-condition','Goal Ensures'))
               and not b.startswith("Goal Assertion 'missing_return'")]
    valid = lambda b:bool(re.search(r'\bProver\b[^\n]*\breturns\s+Valid\b', b))
    return dict(invariant_goals=len(invariants), target_goals=len(targets),
                all_invariants_valid=all(map(valid,invariants)),
                all_targets_valid=bool(targets) and all(map(valid,targets)),
                invalid_headers=[b.splitlines()[0] for b in invariants+targets if not valid(b)])


def main():
    manifest = read(RUN/'manifest.json')
    original_extract, schedules = native_functions()
    counts = Counter()
    issues = []
    proof_rows = []
    records = sorted((RUN/'tasks').glob('*.json'))
    assert len(records)==832
    for path in records:
        state=read(path)
        key=state['suite']+'_'+str(state['case_id'])
        assert state['status']=='complete' and state['protocol_fingerprint']==manifest['protocol_fingerprint']
        source=REPO/'src/input'/state['suite']/(str(state['case_id'])+'.c')
        assert digest(source)==state['source_sha256']
        # Identify the exact immutable response file through the manifest hash.
        task_fragment='/artifacts/loopy/'+state['suite']+'/'+str(state['case_id'])+'/'
        response_files=[Path(p) for p,h in manifest['input_files'].items()
                        if task_fragment in p and p.endswith('/responses.json') and h==state['responses_sha256']]
        assert len(response_files)==1
        response_path=response_files[0]
        assert digest(response_path)==state['responses_sha256']
        responses=read(response_path)
        parsed={arm:{stage:[] for stage in ['initial','repairs']} for arm in ['native','fixed']}
        for stage in ['initial','repairs']:
            for text in responses[stage]:
                original=original_extract(text)
                fallback=extract_invariants(text,max_invariants=1000)
                fixed=original if original else fallback
                parsed['native'][stage].append(original)
                parsed['fixed'][stage].append(fixed)
                counts['responses_checked']+=1
                if original:
                    assert fixed==original
                    counts['native_nonempty_preserved']+=1
                elif fixed:
                    counts['empty_native_recovered']+=1
        hidden_files=[Path(p) for p,h in manifest['input_files'].items()
                      if task_fragment in p and p.endswith('/input.hidden.c') and h==state['hidden_source_sha256']]
        assert len(hidden_files)==1
        seed=int(state['hidden_source_sha256'][:16],16)
        subset_indices=[indices for indices,_ in schedules(parsed['native']['initial'],seed)]
        for arm in ['native','fixed']:
            variant=state['variants'][arm]
            trace=variant['candidates']
            for t in trace:
                if t['stage']=='initial':
                    assert t['indices']==subset_indices[t['index']]
                    clauses=[c for i in t['indices'] for c in parsed[arm]['initial'][i]]
                else:
                    clauses=parsed[arm]['repairs'][t['index']]
                union=dedup_normalized(clauses)
                assert objhash(union)==t['union_sha256']
                candidate=state['candidate_cache'][t['union_sha256']]
                assert candidate['union']==union and candidate['judgment']['verified']==t['verified']
            assert variant['verified']==any(t['verified'] is True for t in trace)
            counts[arm+'_success']+=variant['verified']
        counts['paired_losses']+=state['variants']['native']['verified'] and not state['variants']['fixed']['verified']
        if not state['variants']['fixed']['verified']:
            continue
        trace=state['variants']['fixed']['candidates'][-1]
        candidate=state['candidate_cache'][trace['union_sha256']]
        assert candidate['judgment']['verified'] is True
        survivors=candidate['survivors']
        assert candidate['judgment']['invariants']==survivors
        assert all(not any(marker in inv for marker in ['/*','*/','#',';','{','}']) for inv in survivors)
        source_text=source.read_text()
        prog=parse_program(source_text)
        annotated=build_annotated(prog,survivors,0)
        loop=prog.loops[0]
        assert annotated.startswith(source_text[:loop.kw_start])
        assert annotated.endswith(source_text[loop.kw_start:])
        origin=candidate['origin']
        entries=[]
        with gzip.open(origin['log_path'],'rt') as log:
            for line in log:
                event=json.loads(line)
                phase=event.get('phase',{})
                if origin['kind']=='local_verification':
                    matches=phase==origin['phase']
                else:
                    matches=phase.get('variant')==origin['arm'] and phase.get('candidate')==origin['candidate']
                args=event.get('args')
                if matches and isinstance(args,list) and '-wp' in args:
                    entries.append(event)
        if not entries:
            issues.append(dict(key=key,error='No matching WP proof command'))
            continue
        final=entries[-1]
        flags=final['args']
        # The final judge writes rlinfer_*, unlike the preceding Houdini jobs.
        if not any('rlinfer_' in str(a) for a in flags):
            issues.append(dict(key=key,error='Last matching WP is not final target judge',args=flags))
        stats=proof_blocks(final.get('stdout') or '')
        expected=2*len(extract_invariants(annotated))
        row=dict(key=key,expected_invariant_goals=expected,**stats,
                 returncode=final.get('returncode'),origin=origin,
                 original_sha256=digest(source),annotated_sha256=hashlib.sha256(annotated.encode()).hexdigest())
        proof_rows.append(row)
        if (row['returncode']!=0 or not stats['all_invariants_valid'] or
                not stats['all_targets_valid'] or stats['invariant_goals']!=expected):
            issues.append(dict(key=key,error='Independent proof-log checks failed',details=row))
    changed=[p for p,h in manifest['input_files'].items() if digest(p)!=h]
    source_changed=[p for p,h in manifest['source_hashes'].items() if digest(REPO/p)!=h]
    result=dict(checked_utc=datetime.now(timezone.utc).isoformat(),
                state='pass' if not issues and not changed and not source_changed else 'needs_attention',
                counts=dict(counts),programs_checked=len(records),positive_proofs_checked=len(proof_rows),
                total_positive_target_goals=sum(r['target_goals'] for r in proof_rows),
                total_positive_invariant_goals=sum(r['invariant_goals'] for r in proof_rows),
                issues=issues,changed_original_inputs=changed,changed_verification_sources=source_changed,
                scope='Independent reconstruction of both extraction arms and parsing of existing final WP proof logs; no new proof/model calls.',
                limitation='This audits cached trajectory replay, not a fresh adaptive Loopy run. It does not establish historical upstream reasoning configuration.')
    (OUT/'reliability_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (OUT/'positive_proof_audit.json').write_text(json.dumps(proof_rows,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    main()
