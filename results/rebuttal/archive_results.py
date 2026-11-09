"""Archive completed rebuttal experiments without changing original runs."""
import csv
import gzip
import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from collections import Counter
from pathlib import Path

REPO = Path('/home/yangfp/CRAFT')
WORKSPACE = Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20')
OUT = WORKSPACE / 'outputs'
DEST = REPO / 'results/rebuttal'
PART_SIZE = 24 * 1024 * 1024
provenance = []
archives = []
secret_patterns = [re.compile(rb'sk-[A-Za-z0-9_-]{20,}'),
                   re.compile(rb'Bearer\s+[A-Za-z0-9_.-]{24,}'),
                   re.compile(rb'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----')]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def scan(data, name):
    if any(p.search(data) for p in secret_patterns):
        raise RuntimeError('Possible credential in ' + name)
    if name.endswith('.gz') and not name.endswith('.tar.gz'):
        scan(gzip.decompress(data), name[:-3])


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def copy_file(src, dst):
    data = src.read_bytes()
    scan(data, str(src))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    provenance.append({'source': str(src), 'destination': str(dst.relative_to(DEST)),
                       'bytes': len(data), 'sha256': digest(data)})


def copy_tree(src, dst, exclude=()):
    for p in sorted(src.rglob('*')):
        if not p.is_file() or p.name in exclude:
            continue
        if p.is_symlink():
            raise RuntimeError('Unexpected symlink: ' + str(p))
        copy_file(p, dst / p.relative_to(src))


def bundle(src, dst, name, source_description):
    """Validate members, split unchanged tar bytes, and keep a complete index."""
    dst.mkdir(parents=True, exist_ok=True)
    members = []
    with tarfile.open(src, 'r:gz') as tf:
        for member in tf:
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts or member.issym() or member.islnk():
                raise RuntimeError('Unsafe archive member: ' + member.name)
            if member.isfile():
                if path.name == '.env' or path.name.startswith('.env.'):
                    raise RuntimeError('Environment file in archive: ' + member.name)
                data = tf.extractfile(member).read()
                scan(data, member.name)
                members.append({'path': member.name, 'bytes': len(data), 'sha256': digest(data)})
    parts = []
    whole = hashlib.sha256()
    with src.open('rb') as stream:
        i = 0
        while data := stream.read(PART_SIZE):
            part = name + '.part' + str(i).zfill(3)
            (dst / part).write_bytes(data)
            whole.update(data)
            parts.append({'file': part, 'bytes': len(data), 'sha256': digest(data)})
            i += 1
    info = {'archive': name, 'source': source_description, 'bytes': src.stat().st_size,
            'sha256': whole.hexdigest(), 'parts': parts, 'file_count': len(members),
            'members': members}
    write_json(dst / (name + '.index.json'), info)
    archives.append({'index': str((dst / (name + '.index.json')).relative_to(DEST)),
                     'sha256': whole.hexdigest(), 'file_count': len(members)})
    print('BUNDLE', name, len(members), 'files', round(src.stat().st_size / 1024**2, 1), 'MiB', flush=True)


def pack_tree(src, dst, name, exclude_dirs=()):
    with tempfile.NamedTemporaryFile(suffix='.tar.gz') as temp:
        with open(temp.name, 'wb') as raw:
            with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0, compresslevel=6) as gz:
                with tarfile.open(fileobj=gz, mode='w|') as tf:
                    for p in sorted(src.rglob('*')):
                        rel = p.relative_to(src)
                        if not p.is_file() or any(x in exclude_dirs for x in rel.parts):
                            continue
                        if p.is_symlink():
                            raise RuntimeError('Unexpected symlink: ' + str(p))
                        tf.add(p, arcname=str(rel), recursive=False)
        bundle(Path(temp.name), dst, name, str(src))


def scripts(target, names):
    for name in names:
        copy_file(WORKSPACE / 'work' / name, target / 'scripts' / name)


if DEST.exists():
    raise SystemExit('Destination already exists; inspect before rerunning: ' + str(DEST))
DEST.mkdir()
head = subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip()

a = DEST / '01_craft16_generation'
copy_tree(OUT / 'craft16_minimal_v2', a,
          exclude=('evidence_after_recovery_20261108.tar.gz', 'evidence_after_recovery_20261108.sha256.json',
                   'evidence_complete_20261108.tar.gz'))
bundle(OUT / 'craft16_minimal_v2/evidence_complete_20261108.tar.gz', a / 'evidence',
       'evidence_complete_20261108.tar.gz', str(OUT / 'craft16_minimal_v2/evidence_complete_20261108.tar.gz'))
scripts(a, ['craft16_minimal_v2.py', 'export_craft16.py', 'recover_craft16_remaining.py',
            'finalize_craft16_remaining.py', 'retry_craft16_failed_records.py',
            'retry_craft16_small_followups.py', 'finalize_craft16_user_retry.py'])

b = DEST / '02_craft16_same_pool_prefixes'
copy_tree(OUT / 'craft16_prefix_trend', b)
pack_tree(Path('/home/yangfp/craft_empirical_runs/craft16_prefix_trend_20261108'), b / 'evidence',
          'prefix_verification.tar.gz', exclude_dirs=('scratch',))
scripts(b, ['craft_prefix_trend.py'])

c = DEST / '03_loopy_extraction_fix'
copy_tree(OUT / 'loopy_local_verify', c, exclude=('verification_evidence.tar.gz',))
bundle(OUT / 'loopy_local_verify/verification_evidence.tar.gz', c / 'evidence',
       'verification_evidence.tar.gz', str(OUT / 'loopy_local_verify/verification_evidence.tar.gz'))
usage = json.loads((c / 'cached_usage.json').read_text())
for row in usage['per_program']:
    src = Path(row['historical_api_usage_path'])
    data = src.read_bytes()
    if digest(data) != row['historical_api_usage_sha256']:
        raise RuntimeError('Loopy historical usage hash changed: ' + str(src))
    copy_file(src, c / 'historical_api_usage' / (row['suite'] + '_' + str(row['case_id']) + '.json'))
scripts(c, ['loopy_cached_local_verify.py', 'audit_loopy_replay_reliability.py',
            'export_loopy_local_verify.py', 'archive_loopy_verification.py'])

d = DEST / '04_composition_vs_iterative_repair'
copy_tree(OUT / 'loopy_compose_refine_20261107', d, exclude=('evidence.tar.gz',))
bundle(OUT / 'loopy_compose_refine_20261107/evidence.tar.gz', d / 'evidence',
       'evidence.tar.gz', str(OUT / 'loopy_compose_refine_20261107/evidence.tar.gz'))
scripts(d, ['loopy_empirical.py', 'audit_loopy_empirical.py'])

e = DEST / '05_best_single_vs_composition'
copy_tree(OUT / 'bestof_compose_loopy466_20261109', e,
          exclude=('pilot_per_program.csv', 'pilot_report.txt', 'pilot_result.json'))
pack_tree(Path('/home/yangfp/craft_empirical_runs/bestof_compose_loopy466_20261109'), e / 'evidence',
          'candidate_verification.tar.gz', exclude_dirs=('scratch',))
scripts(e, ['bestof_compose_offline.py', 'finalize_bestof_compose.py', 'audit_bestof_wp_logs.py'])

f = DEST / '06_invariant_semantic_audit'
copy_tree(OUT / 'craft16_invariant_audit_20261109', f)
copy_tree(OUT / 'semantic_original_discrepancy_audit', f / 'original_verification_diagnostics')
scripts(f, ['analyze_craft16_semantics.py', 'replay_craft16_certificates.py',
            'summarize_craft16_semantics.py', 'test_craft16_semantics.py',
            'investigate_semantic_discrepancies.py'])

g = DEST / '07_reasoning_budget_comparison'
copy_file(OUT / 'reasoning_medium_prefix_usage_audit.json', g / 'medium_prefix_usage.json')
copy_file(OUT / 'reasoning_setting_audit.json', g / 'reasoning_setting_audit.json')
medium = REPO / 'results/02_rq2_tool_comparison/gpt5nano_full832'
for name in ['grid_recompute.jsonl', 'grid_summary.json', 'protocol.json', 'r10_protocol.json',
             'events/loopgym_r10_houdini.jsonl']:
    copy_file(medium / name, g / 'historical_medium' / name)
copy_tree(medium / 'artifacts/loopgym_r10_houdini', g / 'historical_medium/artifacts/loopgym_r10_houdini')
minimal = json.loads((b / 'generation_prefix_token_costs.json').read_text())['by_k']['16']
med = json.loads((g / 'medium_prefix_usage.json').read_text())['prefixes']['1']
fresh = json.loads((a / 'result.json').read_text())['by_suite']['all']
compare = {'population': 832, 'new_api_calls': 0,
           'minimal_compose16': {'verified': fresh['success'], 'rate': fresh['success']/832,
                                  'mean_tokens': minimal['mean_tokens_per_program']},
           'medium_compose1': {'verified': med['verified'], 'rate': med['rate'],
                               'mean_tokens': med['mean_total_tokens']},
           'verification_difference_pp': (fresh['success']/832-med['rate'])*100,
           'token_difference_percent': (minimal['mean_tokens_per_program']/med['mean_total_tokens']-1)*100,
           'scope': 'Cross-configuration comparison at similar recorded token cost; prompting and response batches differ. Medium prefixes share their own pool.'}
write_json(g / 'comparison.json', compare)

docs = {
    a: ('Fresh CRAFT@16 generation', '832 programs; GPT-5-nano/minimal; 16 responses in four n=4 calls per program. Final success: 602/832. The final evidence bundle includes generations, source programs, verification records, and logs. Recovery addenda retain failed/retried request history; known usage may omit unreported failed-attempt billing.'),
    b: ('CRAFT same-pool prefixes', 'Prefixes 1/4/8/16 of the final pool in ../01_craft16_generation. Success counts: 386/513/555/602 out of 832. No new generation. Prefix 1 token cost is estimated because responses were generated in n=4 batches; k=4/8/16 use recorded batch usage. The evidence bundle contains local verification records and logs; temporary scratch directories are omitted.'),
    c: ('Loopy invariant-extraction correction', 'Local re-verification of cached initial and repair responses, not regeneration of adaptive repair trajectories. Corrected initial composition verifies 553/832; including existing repairs verifies 566/832. cached_usage.json attributes historical usage to accessed responses; re-verification made no new model calls. historical_api_usage/ contains all 832 original usage files, each checked against the previously recorded hash. See protocol_limitations.txt for the cached-replay interpretation.'),
    d: ('Controlled composition versus iterative repair', '466 Loopy programs, GPT-5-nano/minimal, hidden/visible targets, and 1/4/8 calls. Both methods share the initial response within each visibility setting. Composition pools independent responses; repair revises sequentially and applies Houdini to the final candidate. Calls are matched; tokens/time are measured. Final-output and any-checkpoint results are kept separate. The bundle contains model replies, usage, feedback, scores, verification logs, and sources.'),
    e: ('Best single versus composition', '466 Loopy programs and prefixes 1/4/8/16 of the final CRAFT@16 pool. Compare highest-negative-coverage independent response, any independently successful response, and joint clause composition. No new generation or training. Candidate generation is matched; verifier compute can differ. This probe measures label-construction behavior, not the downstream effect of retraining SFT. The bundle retains per-program records, frozen negative samples, proof records, and logs; temporary scratch and preliminary pilot reports are omitted.'),
    f: ('Invariant semantic audit', 'Frozen CRAFT@16 invariants, analyzed under explicitly modeled defined 32-bit scalar-integer semantics. Rebuttal statistics use only the 602 originally verified programs: 396 strictly stronger than the target obligation and 304 reachability-exact. Complete 832-program audit records are preserved for provenance. Saved SMT obligations were rechecked by a separate script using Z3; this is not an independent C semantic translation or proof-kernel certification. original_verification_diagnostics/ explains the additional seven strict/eight exact cases outside the original-success subset, without changing original outcomes.'),
    g: ('Reasoning-budget accounting', 'Offline accounting of the historical medium pool and comparison with the new minimal pool. All evaluations cover 832 programs. Medium compose@1/@4/@8: 483/640/669 verified. Tokens include input and output for every call; cumulative API time excludes verifier time and is not parallel wall-clock latency. minimal@16 versus medium@1 is a supplementary cross-batch, cross-prompt comparison, not an isolated reasoning ablation. Historical event/API records and grid results are copied here for auditability; no new generation was performed.')
}
for target, (title, text) in docs.items():
    # Do not replace copied experiment-specific README files.
    name = 'ARCHIVE.md' if (target / 'README.md').exists() else 'README.md'
    (target / name).write_text('# ' + title + '\n\n' + text + '\n\n'
        'Scripts are retained as run. They contain historical local paths; restore the evidence and adapt those path constants before reproduction. '
        'Archiving did not call a model or rerun training. For split evidence bundles, use `../restore_evidence.py` with the bundle index and an output directory.\n')

copy_file(WORKSPACE / 'work/restore_rebuttal_evidence.py', DEST / 'restore_evidence.py')
copy_file(WORKSPACE / 'work/archive_rebuttal_results.py', DEST / 'archive_results.py')
write_json(DEST / 'provenance.json', {'archived_on': '2026-11-10', 'repository_head_before_archive': head,
           'copied_files': provenance, 'bundles': archives,
           'scope': 'Completed new rebuttal experiments and supporting offline analyses. Original chapter-organized results are unchanged. Author-supplied historical Shapley summaries are not new experiments performed by this archive.'})

(DEST / 'README.md').write_text('''# ICLR 2027 rebuttal experiments (November 2026)

This directory keeps rebuttal additions separate from the original chapter-organized
`results/01_rq1_*` through `results/05_appendix_*` experiments. It preserves completed
results, protocols, per-program data, costs, scripts, and evidence; it does not replace
the original paper tables or experimental records.

| Directory | Experiment | Rebuttal use |
|---|---|---|
| [01_craft16_generation](01_craft16_generation/) | Fresh GPT-5-nano/minimal, 832 programs, 16 responses | Shared Loopy comparison |
| [02_craft16_same_pool_prefixes](02_craft16_same_pool_prefixes/) | Same-pool compose@1/4/8/16, token accounting | Shared Loopy comparison and sampling trend |
| [03_loopy_extraction_fix](03_loopy_extraction_fix/) | Corrected extraction, cached-response re-verification | R1/R2/R3 Q2 |
| [04_composition_vs_iterative_repair](04_composition_vs_iterative_repair/) | Hidden/visible targets, matched 1/4/8 calls, 466 programs | R2 Q1 |
| [05_best_single_vs_composition](05_best_single_vs_composition/) | Matched candidate pool, highest-coverage single versus composition | R1/R3 Q1 |
| [06_invariant_semantic_audit](06_invariant_semantic_audit/) | Strength, reachability exactness, SMT rechecks | R1/R2 Q4 |
| [07_reasoning_budget_comparison](07_reasoning_budget_comparison/) | Medium-prefix cost audit and similar-token minimal/medium comparison | R2 Q3 |

## Evidence and reproduction

Every copied source is listed in `provenance.json`. `manifest.json` records SHA-256
hashes for every archived file. Existing compressed bundles are preserved byte for
byte, divided into at most 24 MiB parts. Each `*.index.json` records the complete
archive hash, ordered parts, and member hashes. To restore a bundle:

```sh
python3 results/rebuttal/restore_evidence.py \\
  results/rebuttal/01_craft16_generation/evidence/evidence_complete_20261108.tar.gz.index.json \\
  --out /tmp/rebuttal_craft16_evidence
```

Scripts retain their original run paths and settings. The completed generation,
verification, and cost records can be inspected without making new API calls.
Restore evidence and adapt historical path constants before rerunning a script.
The original model-generation scripts require the original service configuration;
local proof rechecks require the recorded Frama-C/Why3/SMT environment.

Final runs and their recovery histories are retained. Superseded pilot outputs,
duplicate pre-completion bundles, connectivity probes, temporary verifier scratch
directories, and unrelated untracked repository files are excluded. Original data
and historical reward/seed metadata are not edited. This archive does not create
new evidence for the author-supplied Shapley repeat-training claims.

The semantic audit retains all 832 records for traceability; the rebuttal denominator
is the 602 originally verified programs. Loopy repair results reuse existing replies;
costs describe accessed cached calls. The reasoning-budget comparison uses different
prompt/batch configurations. See each experiment's README/ARCHIVE and protocol files.
''')

# Verify copied sources are unchanged after archiving.
for item in provenance:
    if digest(Path(item['source']).read_bytes()) != item['sha256']:
        raise RuntimeError('Source changed during archive: ' + item['source'])
manifest = []
for p in sorted(DEST.rglob('*')):
    if p.is_file():
        data = p.read_bytes()
        manifest.append({'path': str(p.relative_to(DEST)), 'bytes': len(data), 'sha256': digest(data)})
write_json(DEST / 'manifest.json', {'files': manifest, 'total_bytes': sum(x['bytes'] for x in manifest),
                                  'file_count': len(manifest), 'hash': 'sha256'})
print('COMPLETE', len(manifest), 'files', round(sum(x['bytes'] for x in manifest)/1024**2, 2), 'MiB', flush=True)
