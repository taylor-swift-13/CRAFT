"""Read existing timing evidence only; no model calls, verifier, or SMT execution.

Run with WSL Python. Writes only the two new timing-report files. Cumulative
worker seconds are elapsed worker time, not CPU time or concurrent wall time.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

WORK = Path(__file__).resolve().parent.parent
RUNS = Path('/home/yangfp/craft_empirical_runs')
INPUTS = {}


def read(path):
    path = Path(path)
    data = path.read_bytes()
    INPUTS[str(path)] = hashlib.sha256(data).hexdigest()
    return json.loads(data)


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def intervals_summary(intervals):
    intervals = sorted((timestamp(a), timestamp(b)) for a, b in intervals if a and b)
    if not intervals:
        return None
    if any(b < a for a, b in intervals):
        raise ValueError('Negative timing interval')
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return dict(started_utc=iso(intervals[0][0]), finished_utc=iso(max(b for a, b in intervals)),
                calendar_span_seconds=max(b for a, b in intervals)-intervals[0][0],
                union_active_seconds=sum(b-a for a, b in merged),
                intervals=len(intervals), disjoint_intervals=len(merged))


def arm_summary(run, prefix, audit):
    manifest = read(run/'manifest.json')
    keys = sorted(Path(p).parent.name+'_'+Path(p).stem for p in manifest['original_inputs'])
    gens = [read(run/'generations'/(key+'.json')) for key in keys]
    if any(g.get('fingerprint') != manifest['fingerprint'] for g in gens):
        raise ValueError('Mixed generation fingerprints')
    calls = [c for g in gens for c in g.get('calls', [])]
    successful = [c for c in calls if isinstance(c.get('successful_request_seconds'), (int, float))]
    invocations = [dict(path=str(p), **read(p)) for p in sorted((run/'invocations').glob('*.json'))]
    prefix_invocations = [dict(path=str(p), **read(p)) for p in sorted((prefix/'invocations').glob('*.json'))]
    generation = dict(expected_programs=len(keys), completed_programs=sum(g['status']=='complete' for g in gens),
        accepted_batches=len(calls), accepted_responses=sum(len(c.get('raw_responses', [])) for c in calls),
        successful_request_seconds=sum(c['successful_request_seconds'] for c in successful) if len(successful)==len(calls) else None,
        successful_request_timing_covered_batches=len(successful),
        accepted_batch_seconds_including_in_batch_retries=sum(c.get('seconds', 0) for c in calls),
        failed_request_events_with_no_individual_duration=sum(len(g.get('failed_request_history', [])) for g in gens),
        calendar=intervals_summary([(g.get('created_utc'), g.get('updated_utc')) for g in gens]),
        imported_programs=sum(bool(g.get('imported_from')) for g in gens),
        note='Batch seconds cover accepted batches; abandoned failed batches/resume gaps are not fully timed. Do not divide batch latency by four to infer standalone @1 latency.')
    rows = {}
    all_intervals = []
    for k in [1, 4, 8, 16]:
        records = [read(run/'verification'/(key+'.json') if k==16 else prefix/'verification'/f'{key}_k{k}.json') for key in keys]
        complete = [v for v in records if v.get('status')=='complete']
        intervals = [(v.get('started_utc'), v.get('updated_utc')) for v in complete]
        all_intervals += intervals
        audit_records = [read(p) for p in sorted((audit/f'k{k}'/'cases').glob('*.json'))]
        replay_records = [read(p) for p in sorted((audit/f'k{k}'/'replay').glob('*.json'))]
        seconds = [v['seconds'] for v in complete if isinstance(v.get('seconds'), (int, float))]
        judges = [v.get('judgment', {}).get('judge_seconds') for v in complete]
        judges = [v for v in judges if isinstance(v, (int, float))]
        rows[str(k)] = dict(expected_programs=len(keys), verification_complete=len(complete),
            success=sum(v.get('verified') is True for v in complete),
            recorded_worker_seconds=sum(seconds), worker_timing_covered=len(seconds),
            mean_worker_seconds=sum(seconds)/len(seconds) if seconds else None,
            final_judge_worker_seconds=sum(judges), final_judge_timing_covered=len(judges),
            proof_task_intervals=intervals_summary(intervals),
            semantic_analysis_records=len(audit_records), semantic_analysis_worker_seconds=sum(x.get('seconds', 0) for x in audit_records),
            semantic_replay_records=len(replay_records), semantic_replay_worker_seconds=sum(x.get('seconds', 0) for x in replay_records))
    semantic_summary = read(audit/'summary.json') if (audit/'summary.json').exists() else None
    return dict(run=str(run), prefix_run=str(prefix), semantic_run=str(audit),
        model=manifest['model'], reasoning_effort=manifest['reasoning_effort'], wp=manifest['wp'],
        generation=generation, by_prefix=rows, invocations=invocations, prefix_invocations=prefix_invocations,
        all_proof_task_intervals=intervals_summary(all_intervals),
        all_proof_worker_seconds=sum(x['recorded_worker_seconds'] for x in rows.values()),
        semantic_full_benchmark_complete=bool(semantic_summary and semantic_summary.get('full_benchmark_complete')),
        semantic_analysis_worker_seconds=sum(x['semantic_analysis_worker_seconds'] for x in rows.values()),
        semantic_replay_worker_seconds=sum(x['semantic_replay_worker_seconds'] for x in rows.values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=WORK/'outputs/craft_visibility_timing_20261110')
    args = parser.parse_args()
    started = datetime.now(timezone.utc).isoformat()
    visible = arm_summary(RUNS/'craft16_target_visible_20261110', RUNS/'craft16_target_visible_prefixes_20261110', RUNS/'craft16_target_visible_semantics_20261110')
    hidden = arm_summary(Path('/tmp/craft16_minimal_20261107_v2'), RUNS/'craft16_prefix_trend_20261108', RUNS/'craft16_hidden_prefix_semantics_control_20261110')
    pipeline = read(WORK/'outputs/craft16_target_visible_20261110/pipeline_runtime.json')
    exported_prefix = read(WORK/'outputs/craft16_target_visible_prefixes_20261110/result.json')
    resumes = []
    for p in sorted((Path(visible['run'])/'resumes').glob('*/addendum.json')):
        start = read(p); end = read(p.parent/'summary.json')
        resumes.append(dict(round=start['round'], workers=start['workers'], selected=len(start['selected']),
            started_utc=start['started_utc'], finished_utc=end['finished_utc'],
            wall_seconds=timestamp(end['finished_utc'])-timestamp(start['started_utc']),
            newly_accepted_batches=end['extra_accepted_batches']))
    late = RUNS/'craft16_target_visible_late_prefix_NLA_lipus_7_20261110'
    late_protocol, late_summary = read(late/'protocol.json'), read(late/'summary.json')
    visible['late_prefix_helper'] = dict(started_utc=late_protocol['utc'], finished_utc=late_summary['utc'], workers=late_protocol['workers'])
    visible['resume_rounds'] = resumes
    visible['pipeline'] = pipeline
    visible['cached_prefix_export_wall_seconds_not_proof_time'] = exported_prefix['wall_seconds']
    prefix_intervals = [(v['started_utc'], v['finished_utc']) for v in visible['prefix_invocations']]
    prefix_intervals.append((late_protocol['utc'], late_summary['utc']))
    visible['prefix_invocation_wall'] = intervals_summary(prefix_intervals)
    first_generation_invocation = min(v['utc'] for v in visible['invocations'] if v['mode']=='generate')
    last_verification = visible['by_prefix']['16']['proof_task_intervals']['finished_utc']
    last_all_proofs = visible['all_proof_task_intervals']['finished_utc']
    visible['end_to_end'] = dict(
        generation_through_compose16=intervals_summary([(first_generation_invocation, last_verification)]),
        generation_through_all_prefix_proofs=intervals_summary([(first_generation_invocation, last_all_proofs)]),
        generation_through_visible_audit_and_export=intervals_summary([(first_generation_invocation, pipeline['finished_utc'])]))
    warnings = [
        'Concurrent wall time is never the sum of request or worker elapsed seconds.',
        'The pipeline prefix stage waited for an existing prefix evaluator lock; its duration overlaps the primary invocation and is not additional proof work.',
        'The final prefix export wall_seconds is a cached resume/export and is not the original proof duration.',
        'Final per-case worker seconds cover the retained completed attempts. Durations of overwritten/interrupted attempts are not recoverable from these records.',
        'Successful API latency is measured separately only in the new visible run. Failed requests lack individual duration; accepted batch spans do not capture every abandoned attempt.',
        'Historical hidden calendar spans include recovery delays, imported pilot responses, and different concurrency. They are not a controlled wall-time speed comparison.',
        'Each prefix is evaluated locally from the same 16-response pool. @1 request latency cannot be obtained by dividing a four-response batch latency by four.',
        'Semantic audit cumulative seconds and wall times are separate from invariant generation and Frama-C verification. A partial hidden audit snapshot is explicitly marked.',
    ]
    result = dict(schema_version=1, snapshot_started_utc=started, generated_utc=datetime.now(timezone.utc).isoformat(),
        arms=dict(visible=visible, hidden=hidden), definitions=dict(
            recorded_worker_seconds='Sum of per-case monotonic elapsed seconds, not CPU seconds; concurrent jobs overlap.',
            calendar_span_seconds='Last completion timestamp minus first start timestamp; includes gaps and queue/recovery delays.',
            union_active_seconds='Union of recorded time intervals; overlapping intervals counted once.'), warnings=warnings,
        source_script=str(Path(__file__).resolve()), source_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        input_sha256=INPUTS)
    # Files already read must still match. Newly produced hidden audit records do
    # not invalidate this explicit snapshot, but mutation of an included input does.
    for path, checksum in INPUTS.items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=checksum:
            raise RuntimeError('Input changed during read-only aggregation; run again: '+path)
    g=visible['generation']; end=visible['end_to_end']; p=visible['prefix_invocation_wall']
    lines=['# CRAFT visibility timing evidence', '', 'All timestamps below are UTC. This report reads existing records only; it performs no generation or verification.', '',
        f"Visible generation: {g['completed_programs']}/832 programs, {g['accepted_batches']:,} accepted batches ({g['accepted_responses']:,} responses).",
        f"Generation calendar span: {g['calendar']['started_utc']} to {g['calendar']['finished_utc']} = {g['calendar']['calendar_span_seconds']/60:.2f} minutes.",
        f"Successful API requests: {g['successful_request_seconds']:,.2f} cumulative seconds ({g['successful_request_seconds']/3600:.2f} request-hours). Accepted batch spans, including retries within completed batches: {g['accepted_batch_seconds_including_in_batch_retries']:,.2f} seconds. These are not concurrent wall times; {g['failed_request_events_with_no_individual_duration']} failed-request events have no individual duration.", '',
        '| Arm | Prefix | Verified | Success | Local verification worker-hours | Mean seconds/program |',
        '|---|---:|---:|---:|---:|---:|']
    for name, arm in [('visible', visible), ('hidden (historical)', hidden)]:
        for k,row in arm['by_prefix'].items():
            lines.append(f"| {name} | {k} | {row['verification_complete']}/832 | {row['success']} | {row['recorded_worker_seconds']/3600:.3f} | {row['mean_worker_seconds']:.2f} |")
    lines += ['', f"Visible @16 local verification totals {visible['by_prefix']['16']['recorded_worker_seconds']/3600:.2f} worker-hours. The three additional prefix evaluations total {sum(visible['by_prefix'][str(k)]['recorded_worker_seconds'] for k in [1,4,8])/3600:.2f} worker-hours. These jobs ran concurrently and these totals are not summed as wall time.", '',
        f"Actual prefix evaluator invocation span: {p['started_utc']} to {p['finished_utc']} = {p['calendar_span_seconds']/60:.2f} minutes. This includes the late-case helper and cache-only resume; overlapping intervals are counted once ({p['union_active_seconds']/60:.2f} minutes active union). The final exported {exported_prefix['wall_seconds']:.6f} seconds is cache/export time only.", '',
        '| Visible end-to-end milestone | Elapsed minutes from first generation invocation |', '|---|---:|']
    for label,key in [('Generation plus @16 proof', 'generation_through_compose16'),('Generation plus all prefix proofs', 'generation_through_all_prefix_proofs'),('Generation, all prefix proofs, semantic audit and export', 'generation_through_visible_audit_and_export')]:
        lines.append(f"| {label} | {end[key]['calendar_span_seconds']/60:.2f} |")
    lines += ['', 'Recorded pipeline stages (not additive when they overlap other invocations):', '', '| Stage | Start UTC | End UTC | Process wall minutes |', '|---|---|---|---:|']
    for stage in pipeline['stages']:
        lines.append(f"| {stage['name']} | {stage['started_utc']} | {stage.get('finished_utc', 'pending')} | {stage.get('wall_seconds', 0)/60:.3f} |")
    lines += ['', f"Visible semantic audit complete: {visible['semantic_full_benchmark_complete']}; analysis {visible['semantic_analysis_worker_seconds']/3600:.3f} worker-hours, independent replay {visible['semantic_replay_worker_seconds']/3600:.3f} worker-hours. Hidden control audit complete at snapshot: {hidden['semantic_full_benchmark_complete']}.", '', 'Interpretation limits:', '']
    lines += ['- '+warning for warning in warnings]
    lines += ['', 'Recompute with WSL Python:', '', '```text', f'python3 -B {Path(__file__).resolve()}', '```', '', 'Every consumed JSON input and its SHA-256 is recorded in result.json, together with invocation/resume times and per-prefix task timing boundaries.']
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out/'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n')
    (args.out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(dict(output=str(args.out), visible_api_seconds=g['successful_request_seconds'],
        visible_proof_worker_seconds=visible['all_proof_worker_seconds'], prefix_wall_seconds=p['calendar_span_seconds'],
        end_to_end=end, input_files=len(INPUTS)), indent=2))


if __name__=='__main__':
    main()
