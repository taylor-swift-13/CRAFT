import hashlib
import json
import tarfile
from pathlib import Path

WS = Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20')
RUN = Path('/home/yangfp/craft_empirical_runs/bestof_compose_loopy466_20261109')
OUT = WS / 'outputs/bestof_compose_loopy466_20261109/cost_audit'
protocol = json.loads((RUN / 'protocol.json').read_text())
archived = {}
archive = WS / 'outputs/craft16_minimal_v2/evidence_complete_20261108.tar.gz'
with tarfile.open(archive) as tf:
    for member in tf:
        if member.isfile() and '/verification/Loopy_' in member.name and member.name.endswith('.json'):
            archived[Path(member.name).name] = tf.extractfile(member).read()

checks, rows = [], []
totals = {n: dict(n=n, programs=0, unique_single_jobs=0, single_seconds=0., compose_seconds=0.,
                 single_excluding_goal_seconds=0., compose_excluding_goal_seconds=0.,
                 single_reused_jobs=0, single_new_child_cpu_seconds=0.) for n in [1,4,8,16]}
for key, case in protocol['cases'].items():
    singles = {}
    for ck in set(case['candidate_keys']):
        d = json.loads((RUN / 'single_proofs' / f'{key}_{ck}.json').read_text())
        assert d['status'] == 'complete'
        singles[ck] = d
    for n, total in totals.items():
        source = case['compose'][str(n)]
        path = Path(source['path'])
        raw = path.read_bytes() if path.exists() else archived[path.name]
        assert hashlib.sha256(raw).hexdigest() == source['sha256'], (key,n,'hash mismatch')
        comp = json.loads(raw)
        candidates = [singles[ck] for ck in dict.fromkeys(case['candidate_keys'][:n])]
        def elapsed(d):
            return d['recorded_original_seconds'] if d.get('reused') else d['seconds']
        st = sum(elapsed(d) for d in candidates)
        sf = sum(elapsed(d) - d['judgment']['judge_seconds'] for d in candidates)
        ct = comp['seconds']
        cf = ct - comp['judgment']['judge_seconds']
        assert sf >= 0 and cf >= 0
        row = dict(key=key,n=n,unique_single_jobs=len(candidates),single_seconds=st,
                   compose_seconds=ct,single_excluding_goal_seconds=sf,compose_excluding_goal_seconds=cf)
        rows.append(row)
        for field in ['unique_single_jobs','single_seconds','compose_seconds','single_excluding_goal_seconds','compose_excluding_goal_seconds']:
            total[field] += row[field]
        total['programs'] += 1
        total['single_reused_jobs'] += sum(bool(d.get('reused')) for d in candidates)
        total['single_new_child_cpu_seconds'] += sum(d.get('child_cpu_seconds',0) for d in candidates if not d.get('reused'))
        checks.append(dict(key=key,n=n,source_sha256_verified=True))
for total in totals.values():
    total['pipeline_ratio'] = total['single_seconds']/total['compose_seconds']
    total['excluding_goal_ratio'] = total['single_excluding_goal_seconds']/total['compose_excluding_goal_seconds']
result = dict(scope='Same 466 programs and candidate pool as best-coverage/composition probe; existing timing records only.',
    metric='Sum of per-task elapsed seconds, not CPU time or parallel wall-clock makespan.',
    exclusions='No LLM generation, negative-trace sampling, or coverage scoring. Excluding-goal estimate subtracts judgment.judge_seconds from task.seconds; includes remaining task overhead.',
    caveats='Independent and joint records were collected in separate runs with different concurrency/system load. No matched CPU-cost measurement for both arms. Identical candidate clause sets are deduplicated within each program/prefix. Reused records are charged their original elapsed time.',
    archive=str(archive),source_hash_checks=len(checks),totals=list(totals.values()),per_program=rows)
OUT.mkdir(exist_ok=True)
(OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
lines=['# Verification timing from the label-construction comparison','',result['metric'],'',result['exclusions'],'',result['caveats'],'',
       '| N | Independent filtering + evaluation (h) | Joint filtering + evaluation (h) | Ratio | Independent excluding goal check (h) | Joint excluding goal check (h) | Ratio |',
       '|---|---:|---:|---:|---:|---:|---:|']
for t in totals.values():
    lines.append(f"| {t['n']} | {t['single_seconds']/3600:.3f} | {t['compose_seconds']/3600:.3f} | {t['pipeline_ratio']:.2f} | {t['single_excluding_goal_seconds']/3600:.3f} | {t['compose_excluding_goal_seconds']/3600:.3f} | {t['excluding_goal_ratio']:.2f} |")
lines += ['',f'All {len(checks)} composition source hashes match the frozen comparison protocol. Original experimental files were not modified.']
(OUT/'REPORT.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='per_program'},indent=2))
