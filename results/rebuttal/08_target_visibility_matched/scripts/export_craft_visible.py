"""Read-only accounting for the matched target-visible response pool."""
import csv
import hashlib
import io
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

RUN = Path('/home/yangfp/craft_empirical_runs/craft16_target_visible_20261110')
OUT = Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20/outputs/craft16_target_visible_20261110')


def read(path):
    return json.loads(path.read_text()) if path.exists() else {}


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def main():
    manifest = read(RUN / 'manifest.json')
    rows, all_calls, failed = [], [], []
    for source in manifest['original_inputs']:
        suite, case = Path(source).parent.name, Path(source).stem
        key = suite + '_' + case
        generation = read(RUN / 'generations' / (key + '.json'))
        verification = read(RUN / 'verification' / (key + '.json'))
        for record in [generation, verification]:
            if record and record['fingerprint'] != manifest['fingerprint']:
                raise ValueError('Protocol mismatch: ' + key)
        calls = generation.get('calls', [])
        all_calls.extend(calls)
        history = generation.get('failed_request_history', [])
        failed.extend(dict(key=key, **event) for event in history)
        usage = [call['usage'] for call in calls if call.get('usage')]
        rows.append(dict(key=key, suite=suite, case_id=case,
            generation_status=generation.get('status', 'pending'),
            responses=sum(len(call['raw_responses']) for call in calls),
            verification_status=verification.get('status', 'pending'),
            verified=verification.get('verified'),
            input_tokens=sum(u.get('prompt_tokens', 0) for u in usage),
            output_tokens=sum(u.get('completion_tokens', 0) for u in usage),
            total_tokens=sum(u.get('total_tokens', 0) for u in usage),
            api_seconds_with_retries=sum(call['seconds'] for call in calls),
            successful_api_seconds=sum(call['successful_request_seconds'] for call in calls),
            verification_seconds=verification.get('seconds'),
            failed_requests=len(history), error=generation.get('error', verification.get('error'))))
    complete = [r for r in rows if r['verification_status'] == 'complete']
    success = sum(r['verified'] is True for r in complete)
    gs = dict(Counter(r['generation_status'] for r in rows))
    vs = dict(Counter(r['verification_status'] for r in rows))
    usage = [call['usage'] for call in all_calls if call.get('usage')]
    changed = [path for path, sha in {**manifest['sources'], **manifest['original_inputs']}.items()
               if hashlib.sha256(Path(path).read_bytes()).hexdigest() != sha]
    report = dict(updated_utc=datetime.now(timezone.utc).isoformat(), expected=832,
        full_benchmark_complete=len(complete) == 832 and not changed,
        generation=gs, verification=vs, success=success,
        full_benchmark_rate=success/832 if len(complete) == 832 else None,
        completed_subset_rate=success/len(complete) if complete else None,
        confirmed_success_lower_bound=success/832,
        unresolved_upper_bound=(success+832-len(complete))/832,
        model=manifest['model'], reasoning_effort=manifest['reasoning_effort'],
        fingerprint=manifest['fingerprint'], accepted_calls=len(all_calls), failed_request_attempts=len(failed),
        missing_usage_calls=len(all_calls)-len(usage),
        input_tokens=sum(u.get('prompt_tokens', 0) for u in usage),
        output_tokens=sum(u.get('completion_tokens', 0) for u in usage),
        total_tokens=sum(u.get('total_tokens', 0) for u in usage),
        cached_input_tokens=sum((u.get('prompt_tokens_details') or {}).get('cached_tokens', 0) or 0 for u in usage),
        reasoning_tokens=sum((u.get('completion_tokens_details') or {}).get('reasoning_tokens', 0) or 0 for u in usage),
        successful_api_seconds=sum(c['successful_request_seconds'] for c in all_calls),
        api_seconds_with_retries=sum(c['seconds'] for c in all_calls),
        verification_seconds=sum(r['verification_seconds'] or 0 for r in complete),
        changed_sources=changed,
        monetary_cost=None,
        billing_note='Actual account charge is not provided in response usage. Failed-request billing is unknown; no assumed tariff is presented as an actual charge.',
        status_note='API rejections and infrastructure errors are unresolved, not proof failures; full rate requires all 832 completed.',
        by_suite={suite: dict(expected=sum(r['suite']==suite for r in rows),
            completed=sum(r['suite']==suite for r in complete),
            success=sum(r['suite']==suite and r['verified'] is True for r in complete))
            for suite in ['linear','NLA_lipus','Loopy']})
    save(OUT / 'result.json', report)
    save(OUT / 'failed_requests.json', failed)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader(); writer.writerows(rows)
    (OUT / 'per_program.csv').write_text(buffer.getvalue())
    print(json.dumps({k:report[k] for k in ['updated_utc','full_benchmark_complete','generation','verification','success','accepted_calls','total_tokens','failed_request_attempts']}))


if __name__ == '__main__':
    main()
