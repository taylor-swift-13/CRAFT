"""Check final WP logs for every newly proved independent candidate."""
import gzip
import hashlib
import json
from pathlib import Path
import re

RUN = Path('/home/yangfp/craft_empirical_runs/bestof_compose_loopy466_20261109')
EXPORT = Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20/outputs/bestof_compose_loopy466_20261109')


def main():
    result = json.loads((EXPORT / 'result.json').read_text())
    assert result['state'] == 'complete' and result['completed_programs'] == 466
    records = []
    failures = []
    for path in sorted((RUN / 'single_proofs').glob('*.json')):
        candidate = json.loads(path.read_text())
        assert candidate['status'] == 'complete'
        if not candidate['verified'] or candidate.get('reused'):
            continue
        log = RUN / 'logs' / (path.stem + '.jsonl.gz')
        try:
            with gzip.open(log, 'rt') as handle:
                calls = [json.loads(line) for line in handle]
            final = calls[-1]
            stdout = final.get('stdout') or ''
            assert final['returncode'] == 0
            assert '-wp-print' in final['args'] and '-wp' in final['args']
            assert final['args'][final['args'].index('-wp-timeout') + 1] == '5'
            assert final['args'][final['args'].index('-wp-par') + 1] == '2'
            counts = re.findall(r'Proved goals:\s*(\d+)\s*/\s*(\d+)', stdout)
            assert counts and int(counts[-1][0]) == int(counts[-1][1]) > 0
            goals = re.split(r'(?m)^Goal ', stdout)[1:]
            # WP includes CFG-proved unreachable properties in its total but
            # does not print a separate formula block for those properties.
            cfg_valid = re.findall(r'(?m)^\[wp\] \[CFG\] Goal (.+?) : Valid \(Unreachable\)\s*$', stdout)
            assert len(goals) + len(cfg_valid) == int(counts[-1][1]), 'WP total differs from formula blocks plus CFG-valid goals'
            assert all(re.search(r'Prover [^\n]* returns Valid\b', goal) for goal in goals)
            target_goals = [g for g in goals if g.startswith(('Assertion ', 'Post-condition ', 'Postcondition '))]
            cfg_targets = [g for g in cfg_valid if re.search(r'(?:^|_)(?:assert|ensures|post)(?:_|$)', g)]
            assert target_goals or cfg_targets, 'No explicit target assertion/postcondition in final WP output'
            assert final.get('c_inputs'), 'Missing annotated program captured with final proof'
            records.append(dict(record=path.name, goals=len(goals) + len(cfg_valid),
                target_goals=len(target_goals) + len(cfg_targets), cfg_unreachable_goals=len(cfg_valid),
                log=log.name, log_sha256=hashlib.sha256(log.read_bytes()).hexdigest()))
        except Exception as error:
            failures.append(dict(record=path.name, error=f'{type(error).__name__}: {error}'))
    audit = dict(status='pass' if not failures else 'fail',
        newly_verified_unique_candidates=len(records),
        final_wp_goals=sum(r['goals'] for r in records),
        explicit_target_goals=sum(r['target_goals'] for r in records),
        cfg_unreachable_goals=sum(r['cfg_unreachable_goals'] for r in records),
        scope='Final WP logs for newly verified independent candidates; reused records retain their original proof provenance.',
        failures=failures, records=records)
    (EXPORT / 'wp_log_audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    print(json.dumps({**{k: v for k, v in audit.items() if k not in {'records', 'failures'}},
        'failure_count':len(failures), 'failure_examples':failures[:5]}))
    assert not failures


if __name__ == '__main__':
    main()
