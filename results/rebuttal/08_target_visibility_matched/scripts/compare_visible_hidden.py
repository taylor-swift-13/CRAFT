"""Read-only comparison of matched hidden/visible CRAFT prefix experiments.

No API, filtering, SMT solving, or training is invoked. Only this script's output
directory is written. Partial runs are explicit; --require-complete rejects them.
The certification gate is loaded from the audited driver without importing its
solver/analysis dependencies. All saved proof/replay hashes are checked.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import io
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

KS = (1, 4, 8, 16)
EXPECTED = 832
RUNS = Path('/home/yangfp/craft_empirical_runs')
WORK = Path(__file__).resolve().parent
READS: dict[str, str] = {}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def capture(path: Path) -> bytes:
    data = path.read_bytes()
    checksum = sha(data)
    name = str(path.resolve())
    if name in READS and READS[name] != checksum:
        raise ValueError('Input changed during comparison: ' + name)
    READS[name] = checksum
    return data


def load(path: Path) -> dict:
    return json.loads(capture(path))


def optional(path: Path) -> dict | None:
    return load(path) if path.exists() else None


def check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def pct(n: int, d: int) -> float | None:
    return 100 * n / d if d else None


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.tmp.{os.getpid()}')
    temporary.write_text(value)
    os.replace(temporary, path)


def write_json(path: Path, value: object) -> None:
    write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def write_csv(path: Path, rows: list[dict]) -> None:
    buffer = io.StringIO()
    fields = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(value) if isinstance(value, (dict, list)) else value
                         for key, value in row.items()})
    write(path, buffer.getvalue())


def load_replay_gate(path: Path):
    """Execute only the two pure classification functions from the actual driver."""
    tree = ast.parse(capture(path).decode())
    wanted = {'required_queries', 'gated_classification'}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in wanted]
    check({node.name for node in functions} == wanted, 'Audit driver lacks replay gate')
    namespace = {}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['required_queries'], namespace['gated_classification']


def validate_manifests(paths: dict, audit_driver: Path, engine: Path):
    manifests = {arm: load(data['run'] / 'manifest.json') for arm, data in paths.items()}
    for arm, manifest in manifests.items():
        body = {key: value for key, value in manifest.items() if key != 'fingerprint'}
        check(sha(json.dumps(body, sort_keys=True).encode()) == manifest.get('fingerprint'),
              arm + ' manifest fingerprint is inconsistent with its fields')
    hidden, visible = manifests['hidden'], manifests['visible']
    fields = ['expected', 'model', 'reasoning_effort', 'endpoint_host', 'n_rollouts',
              'request_schedule', 'max_completion_tokens', 'max_invariants_per_response',
              'unsupported_sampling_parameters_omitted', 'repair', 'wp', 'sources', 'original_inputs']
    for field in fields:
        check(field in hidden and hidden[field] == visible.get(field), 'Generation mismatch: ' + field)
    required = {'expected': 832, 'model': 'gpt-5-nano', 'reasoning_effort': 'minimal',
                'n_rollouts': 16, 'request_schedule': [4, 4, 4, 4],
                'max_completion_tokens': 8192, 'max_invariants_per_response': 20, 'repair': False}
    for field, value in required.items():
        check(hidden[field] == value, 'Unexpected control setting: ' + field)
    check(hidden['wp'] == {'timeout': 5, 'parallel': 2, 'provers': 'alt-ergo,z3'}, 'WP mismatch')
    check(sorted(hidden['unsupported_sampling_parameters_omitted']) == ['temperature', 'top_p'], 'Sampling mismatch')
    check(hidden.get('target_hidden') is True and visible.get('target_hidden') is False, 'Visibility flags mismatch')
    check(visible.get('target_free_filtering') is True and visible.get('target_visibility_scope') == 'generation_only',
          'Visible arm must change generation visibility only')
    check(visible.get('control_fingerprint') == hidden['fingerprint'], 'Wrong hidden control')
    check(visible.get('control_manifest_sha256') == READS[str((paths['hidden']['run'] / 'manifest.json').resolve())],
          'Hidden control manifest hash mismatch')
    expected = {Path(source).parent.name + '_' + Path(source).stem: digest
                for source, digest in hidden['original_inputs'].items()}
    check(len(expected) == EXPECTED, 'Expected 832 unique program keys')
    for source, digest in hidden['sources'].items():
        check(sha(capture(Path(source))) == digest, 'Code/template drift: ' + source)
    prompt_files = {Path(source).name: Path(source) for source in hidden['sources'] if '/prompt/' in source}
    check('system_prompt.txt' in prompt_files and 'generate_prompt.txt' in prompt_files, 'Missing prompt templates')
    templates = {'system': capture(prompt_files['system_prompt.txt']).decode(),
                 'generate': capture(prompt_files['generate_prompt.txt']).decode()}
    driver_hash, engine_hash = sha(capture(audit_driver)), sha(capture(engine))
    audit_protocols, prefix_protocols = {}, {}
    for arm, data in paths.items():
        manifest = manifests[arm]
        prefix = optional(data['prefix'] / 'protocol.json')
        if prefix is not None:
            check(prefix.get('source_fingerprint') == manifest['fingerprint'], arm + ' prefix source mismatch')
            for field in ['model', 'reasoning_effort', 'max_invariants_per_response', 'wp']:
                check(prefix.get(field) == manifest[field], arm + ' prefix setting mismatch: ' + field)
            check(prefix.get('target_hidden') == manifest['target_hidden'], arm + ' prefix visibility mismatch')
        prefix_protocols[arm] = prefix
        audit = optional(data['audit'] / 'protocol.json')
        if audit is not None:
            check(audit.get('source_fingerprint') == manifest['fingerprint'], arm + ' audit source mismatch')
            for field, value in [('analysis_engine_sha256', engine_hash), ('driver_sha256', driver_hash),
                                 ('exact_budget_seconds', 15), ('replay_timeout_ms', 8000), ('schema_version', 2)]:
                check(audit.get(field) == value, arm + ' audit mismatch: ' + field)
            check(set(audit.get('expected_keys', [])) == set(expected), arm + ' audit population mismatch')
            check(audit.get('source_manifest_sha256') == READS[str((data['run'] / 'manifest.json').resolve())],
                  arm + ' audit manifest changed')
        audit_protocols[arm] = audit
    alignment = {'matched_generation_fields': fields, 'programs': EXPECTED,
                 'hidden_fingerprint': hidden['fingerprint'], 'visible_fingerprint': visible['fingerprint'],
                 'analysis_engine_sha256': engine_hash, 'driver_sha256': driver_hash,
                 'exact_budget_seconds': 15, 'replay_timeout_ms': 8000,
                 'semantic_gate': str(audit_driver),
                 'intentional_difference': 'Generation sees original.c in visible arm and hidden.c in hidden arm; filtering remains target-free.',
                 'audit_protocol_present': {a: p is not None for a, p in audit_protocols.items()}}
    return manifests, expected, templates, prefix_protocols, audit_protocols, alignment


def cost(generation: dict | None, k: int) -> dict:
    required = max(1, k // 4)
    selected = (generation or {}).get('calls', [])[:required]
    usable = [c for c in selected if isinstance(c.get('usage'), dict)
              and all(isinstance(c['usage'].get(x), (int, float)) for x in ['prompt_tokens', 'completion_tokens', 'total_tokens'])]
    inputs = sum(c['usage']['prompt_tokens'] for c in usable)
    outputs = sum(c['usage']['completion_tokens'] for c in usable)
    total = sum(c['usage']['total_tokens'] for c in usable)
    if k == 1:
        outputs /= 4
        total = inputs + outputs
    complete = len(selected) == required and len(usable) == required
    return {'cost_complete': complete, 'cost_estimated': k == 1,
            'required_batches': required, 'available_batches': len(selected),
            'missing_batches': required - len(selected), 'missing_usage_batches': len(selected) - len(usable),
            'known_input_tokens': inputs, 'known_output_tokens': outputs, 'known_total_tokens': total,
            'total_tokens': total if complete else None,
            'batch_api_seconds_including_retries': sum(c.get('seconds', 0) for c in selected),
            'successful_request_seconds': sum(c['successful_request_seconds'] for c in selected)
                if selected and all('successful_request_seconds' in c for c in selected) else None}


def collect_arm(arm, paths, manifest, expected, templates, prefix_protocol, audit_protocol, alignment, required_queries, gate):
    rows = []
    # The audit's top-level protocol is only its latest invocation. Earlier
    # prefixes remain usable when their archived invocation has the same engine,
    # driver, budgets and source fingerprint. Never mix legacy audit engines.
    job_index = {}
    for protocol_path in sorted((paths['audit'] / 'invocations').glob('*/protocol.json')):
        previous = load(protocol_path)
        if (previous.get('source_fingerprint') == manifest['fingerprint']
                and previous.get('analysis_engine_sha256') == alignment['analysis_engine_sha256']
                and previous.get('driver_sha256') == alignment['driver_sha256']
                and previous.get('exact_budget_seconds') == 15
                and previous.get('replay_timeout_ms') == 8000
                and previous.get('schema_version') == 2):
            job_index.update({(j['key'], j['k']): j for j in previous.get('inputs', [])})
    job_index.update({(j['key'], j['k']): j for j in (audit_protocol or {}).get('inputs', [])})
    for key, expected_hash in sorted(expected.items()):
        original_path = paths['run'] / 'sources' / (key + '.original.c')
        hidden_path = paths['run'] / 'sources' / (key + '.hidden.c')
        source = capture(original_path)
        hidden_source = capture(hidden_path)
        check(sha(source) == expected_hash, arm + ' original source mismatch: ' + key)
        generation_path = paths['run'] / 'generations' / (key + '.json')
        generation = optional(generation_path)
        if generation is not None:
            check(generation.get('fingerprint') == manifest['fingerprint'], arm + ' generation mismatch: ' + key)
            expected_messages = [{'role': 'system', 'content': templates['system']},
                                 {'role': 'user', 'content': templates['generate'].format(
                                     program=(hidden_source if arm == 'hidden' else source).decode())}]
            if generation.get('messages') is not None:
                check(generation['messages'] == expected_messages, arm + ' actual prompt mismatch: ' + key)
            if generation.get('status') == 'complete':
                check(generation.get('messages') == expected_messages, arm + ' complete prompt missing: ' + key)
                calls = generation.get('calls', [])
                check(len(calls) == 4 and all(c.get('requested_n') == 4 and len(c.get('raw_responses', [])) == 4 for c in calls),
                      arm + ' accepted call schedule mismatch: ' + key)
                check(all(c.get('returned_model', '').startswith('gpt-5-nano') for c in calls),
                      arm + ' returned model mismatch: ' + key)
                check(generation.get('responses') == [s for c in calls for s in c['raw_responses']], arm + ' response order mismatch: ' + key)
        for k in KS:
            vp = paths['run'] / 'verification' / (key + '.json') if k == 16 else paths['prefix'] / 'verification' / f'{key}_k{k}.json'
            verification = optional(vp)
            complete = verification is not None and verification.get('status') == 'complete'
            row = {'arm': arm, 'key': key, 'suite': key.rsplit('_', 1)[0], 'k': k,
                   'generation_status': (generation or {}).get('status', 'missing'),
                   'verification_status': (verification or {}).get('status', 'missing'),
                   'verification_complete': complete, 'verified': None,
                   'audit_completed': False, 'replay_completed': False,
                   'strength': 'audit_missing', 'exact': 'audit_missing',
                   'certified_strict': False, 'certified_exact': False,
                   'source_sha256': expected_hash, 'hidden_source_sha256': sha(hidden_source), **cost(generation, k)}
            if complete:
                check(generation is not None and generation.get('status') == 'complete', arm + ' verification without complete generation: ' + key)
                check(isinstance(verification.get('verified'), bool), arm + ' nonboolean verification: ' + key)
                check(verification.get('key') == key, arm + ' verification key mismatch: ' + key)
                check(verification.get('generation_sha256') == READS[str(generation_path.resolve())], arm + ' stale generation linkage: ' + key)
                if k == 16:
                    check(verification.get('fingerprint') == manifest['fingerprint'], arm + ' verification fingerprint mismatch: ' + key)
                else:
                    check(prefix_protocol is not None and verification.get('k') == k, arm + ' prefix protocol missing/mismatch')
                    base_path = paths['run'] / 'verification' / (key + '.json')
                    check(verification.get('base_verification_sha256') == sha(capture(base_path)),
                          arm + ' prefix base-verification linkage changed: ' + key)
                row['verified'] = verification['verified']
            case_path = paths['audit'] / f'k{k}/cases/{key}.json'
            if not case_path.exists():
                rows.append(row)
                continue
            check(complete and audit_protocol is not None, arm + ' semantic record without complete matching inputs: ' + key)
            case = load(case_path)
            check((key, k) in job_index, arm + ' semantic record absent from audit inventory: ' + key)
            job = job_index[(key, k)]
            proof_path = paths['audit'] / f'k{k}/proof_queries/{key}.json'
            check(proof_path.exists(), arm + ' semantic record without saved queries: ' + key)
            proof_hash = sha(capture(proof_path))
            for field, value in [('key', key), ('k', k), ('source_sha256', expected_hash),
                                 ('verification_sha256', READS[str(vp.resolve())]),
                                 ('analysis_engine_sha256', alignment['analysis_engine_sha256']),
                                 ('driver_sha256', alignment['driver_sha256'])]:
                check(case.get(field) == value and job.get(field) == value, arm + ' audit provenance mismatch: ' + field + ':' + key)
            check(case.get('exact_budget_seconds') == 15 and case.get('schema_version') == 2, arm + ' audit budget/schema mismatch')
            check(case.get('proof_queries_sha256') == proof_hash and case.get('original_verified') == row['verified'], arm + ' semantic record changed: ' + key)
            row['audit_completed'] = True
            replay_path = paths['audit'] / f'k{k}/replay/{key}.json'
            replay = optional(replay_path) or {}
            if replay:
                check(replay.get('solver') == 'Z3', arm + ' unexpected replay solver: ' + key)
                for field, value in [('key', key), ('k', k), ('schema_version', 2), ('timeout_ms', 8000),
                                     ('case_sha256', READS[str(case_path.resolve())]), ('proof_queries_sha256', proof_hash),
                                     ('driver_sha256', alignment['driver_sha256'])]:
                    check(replay.get(field) == value, arm + ' replay mismatch: ' + field + ':' + key)
                for field, obligations in required_queries(case).items():
                    checks = replay.get('queries', {})
                    check(all(label in checks and checks[label].get('expected') == value for label, value in obligations.items()),
                          arm + ' missing/mismatched replay obligations: ' + key)
                    passed = all(checks[label].get('actual') == value for label, value in obligations.items())
                    check(replay.get('properties', {}).get(field) == passed, arm + ' replay property flag mismatch: ' + key)
                row['replay_completed'] = True
                row['solver_version'] = replay.get('solver_version')
            row['strength'] = gate(case, replay, 'strength')
            row['exact'] = gate(case, replay, 'exact')
            row['certified_strict'] = row['strength'] == 'strictly_stronger'
            row['certified_exact'] = row['exact'] == 'proven_exact'
            rows.append(row)
    return rows


def summarize(rows):
    complete = [r for r in rows if r['verification_complete']]
    verified = [r for r in complete if r['verified']]
    audited = [r for r in verified if r['audit_completed']]
    replayed = [r for r in audited if r['replay_completed']]
    strict = sum(r['certified_strict'] for r in verified)
    exact = sum(r['certified_exact'] for r in verified)
    costs = [r for r in complete if r['cost_complete']]
    result = {'expected_programs': EXPECTED, 'verification_complete': len(complete),
              'verified_success': len(verified), 'verification_missing_or_incomplete': EXPECTED - len(complete),
              'success_percent_completed': pct(len(verified), len(complete)),
              'success_percent_full': pct(len(verified), EXPECTED) if len(complete) == EXPECTED else None,
              'audit_completed': sum(r['audit_completed'] for r in rows),
              'replay_completed': sum(r['replay_completed'] for r in rows),
              'verified_audit_completed': len(audited), 'verified_replay_completed': len(replayed),
              'certified_strict_among_verified': strict, 'certified_exact_among_verified': exact,
              'semantic_denominator_verified': len(verified),
              'strict_percent_verified': pct(strict, len(verified)) if len(replayed) == len(verified) else None,
              'exact_percent_verified': pct(exact, len(verified)) if len(replayed) == len(verified) else None,
              'strict_percent_verified_confirmed_lower_bound': pct(strict, len(verified)),
              'exact_percent_verified_confirmed_lower_bound': pct(exact, len(verified)),
              'strength_classes_verified': dict(Counter(r['strength'] for r in verified)),
              'exact_classes_verified': dict(Counter(r['exact'] for r in verified)),
              'cost_programs': len(costs), 'cost_missing_programs_among_completed': len(complete)-len(costs),
              'mean_tokens_completed': sum(r['total_tokens'] for r in costs)/len(costs) if costs else None,
              'tokens_estimated': rows[0]['k'] == 1,
              'missing_usage_batches': sum(r['missing_usage_batches'] for r in rows),
              'known_recorded_or_estimated_tokens_available': sum(r['known_total_tokens'] for r in rows),
              'cost_mean_scope': 'Completed-verification programs with complete usage; population size is cost_programs.'}
    result['full_benchmark_complete'] = (len(complete) == EXPECTED and result['audit_completed'] == EXPECTED
                                         and result['replay_completed'] == EXPECTED)
    return result


def paired_binary(pairs, metric):
    a = [bool(metric(h)) for h, v in pairs]
    b = [bool(metric(v)) for h, v in pairs]
    return {'programs': len(pairs), 'hidden_count': sum(a), 'visible_count': sum(b),
            'both': sum(x and y for x, y in zip(a, b)),
            'hidden_only': sum(x and not y for x, y in zip(a, b)),
            'visible_only': sum(y and not x for x, y in zip(a, b)),
            'neither': sum(not x and not y for x, y in zip(a, b)),
            'hidden_percent': pct(sum(a), len(pairs)), 'visible_percent': pct(sum(b), len(pairs)),
            'visible_minus_hidden_pp': pct(sum(b)-sum(a), len(pairs))}


def paired_result(hidden, visible):
    hs, vs = {r['key']: r for r in hidden}, {r['key']: r for r in visible}
    complete = [(hs[key], vs[key]) for key in sorted(hs)
                if hs[key]['verification_complete'] and vs[key]['verification_complete']]
    audited = [(h, v) for h, v in complete if all(r['audit_completed'] and r['replay_completed'] for r in (h, v))]
    both_success = [(h, v) for h, v in audited if h['verified'] and v['verified']]
    cost_pairs = [(h, v) for h, v in complete if h['cost_complete'] and v['cost_complete']]
    return {'expected_programs': EXPECTED, 'common_complete_programs': len(complete),
            'common_complete_keys': [h['key'] for h, v in complete],
            'verification': paired_binary(complete, lambda r: r['verified']),
            'common_audit_replay_complete_programs': len(audited),
            'common_audit_replay_complete_keys': [h['key'] for h, v in audited],
            'verified_and_strict': paired_binary(audited, lambda r: r['verified'] and r['certified_strict']),
            'verified_and_exact': paired_binary(audited, lambda r: r['verified'] and r['certified_exact']),
            'common_verified_audited_programs': len(both_success),
            'common_verified_audited_keys': [h['key'] for h, v in both_success],
            'strict_on_common_verified': paired_binary(both_success, lambda r: r['certified_strict']),
            'exact_on_common_verified': paired_binary(both_success, lambda r: r['certified_exact']),
            'cost': {'programs': len(cost_pairs),
                     'hidden_mean_tokens': sum(h['total_tokens'] for h, v in cost_pairs)/len(cost_pairs) if cost_pairs else None,
                     'visible_mean_tokens': sum(v['total_tokens'] for h, v in cost_pairs)/len(cost_pairs) if cost_pairs else None},
            'interpretation': 'Every paired metric uses the identical keys in both arms. Separate arm-specific successful subsets are descriptive conditional statistics, not a paired comparison. Unconfirmed properties are not certified; this does not prove their absence.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hidden-run', type=Path, default=Path('/tmp/craft16_minimal_20261107_v2'))
    parser.add_argument('--hidden-prefix', type=Path, default=RUNS/'craft16_prefix_trend_20261108')
    parser.add_argument('--hidden-audit', type=Path, default=RUNS/'craft16_hidden_prefix_semantics_control_20261110')
    parser.add_argument('--visible-run', type=Path, default=RUNS/'craft16_target_visible_20261110')
    parser.add_argument('--visible-prefix', type=Path, default=RUNS/'craft16_target_visible_prefixes_20261110')
    parser.add_argument('--visible-audit', type=Path, default=RUNS/'craft16_target_visible_semantics_20261110')
    parser.add_argument('--audit-driver', type=Path, default=WORK/'audit_visible_prefix_semantics.py')
    parser.add_argument('--engine', type=Path, default=WORK/'analyze_craft16_semantics.py')
    parser.add_argument('--out', type=Path, default=WORK.parent/'outputs/craft_visibility_comparison_20261110')
    parser.add_argument('--require-complete', action='store_true')
    args = parser.parse_args()
    paths = {arm: {kind: getattr(args, arm+'_'+kind).resolve() for kind in ['run', 'prefix', 'audit']}
             for arm in ['hidden', 'visible']}
    out = args.out.resolve()
    check(all(out != base and not out.is_relative_to(base) for item in paths.values() for base in item.values()),
          'Output must be outside all read-only input runs')
    required_queries, gate = load_replay_gate(args.audit_driver)
    manifests, expected, templates, prefixes, audits, alignment = validate_manifests(paths, args.audit_driver, args.engine)
    rows = {arm: collect_arm(arm, paths[arm], manifests[arm], expected, templates, prefixes[arm], audits[arm],
                             alignment, required_queries, gate) for arm in paths}
    for hidden, visible in zip(rows['hidden'], rows['visible']):
        check((hidden['key'], hidden['k'], hidden['source_sha256'], hidden['hidden_source_sha256']) ==
              (visible['key'], visible['k'], visible['source_sha256'], visible['hidden_source_sha256']),
              'Source pair alignment mismatch')
    versions = {r.get('solver_version') for arm in rows.values() for r in arm if r['replay_completed']}
    check(len(versions) <= 1 and None not in versions, 'Replay solver version mismatch')
    alignment['replay_solver_versions'] = sorted(versions)
    arms = {arm: {'by_prefix': {str(k): summarize([r for r in data if r['k'] == k]) for k in KS}}
            for arm, data in rows.items()}
    paired = {str(k): paired_result([r for r in rows['hidden'] if r['k'] == k],
                                    [r for r in rows['visible'] if r['k'] == k]) for k in KS}
    complete = all(row['full_benchmark_complete'] for arm in arms.values() for row in arm['by_prefix'].values())
    if args.require_complete:
        check(complete, 'Comparison is partial; no outputs written (--require-complete)')
    # Input files may be produced concurrently; never silently mix versions.
    for path, checksum in READS.items():
        check(sha(Path(path).read_bytes()) == checksum, 'Input changed during comparison; rerun: ' + path)
    result = {'schema_version': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
              'full_benchmark_complete': complete, 'expected_programs': EXPECTED,
              'alignment': alignment, 'arms': arms, 'paired': paired,
              'definitions': {
                  'certification': 'Uses the exact required_queries/gated_classification functions from the hash-matched audit driver. All required replay obligations and provenance hashes are checked; no SMT solving is performed here.',
                  'conditional_rates': 'Strict/exact among each arm\'s verified successes. These denominators can differ; use paired metrics for an identical-program comparison.',
                  'partial_semantics': 'A conditional point rate is null while audits/replays of verified programs are pending. Confirmed lower bounds, classifications, and completion counts remain explicit.',
                  'cost': 'k=4/8/16 use actual accepted n=4 batch usage. k=1 estimates full first-batch input plus one quarter of its output. Reasoning is included in output, not added twice. Failed-attempt billing may be unavailable; no dollar charge is inferred. Prefix costs overlap.',
                  'time': 'Batch API duration includes retries where recorded; @1 has no separately measured one-choice latency.',
                  'no_new_experiment': 'This script reads existing records only. It does not invoke a model, verifier, semantic analysis, or training.'}}
    summary_rows = [{'arm': arm, 'k': k, **arms[arm]['by_prefix'][str(k)]} for arm in arms for k in KS]
    paired_rows = []
    for k in KS:
        for metric in ['verification', 'verified_and_strict', 'verified_and_exact', 'strict_on_common_verified', 'exact_on_common_verified']:
            paired_rows.append({'k': k, 'metric': metric, **paired[str(k)][metric]})
    lines = ['# Matched target-hidden / target-visible CRAFT comparison', '',
             'Status: **' + ('complete' if complete else 'partial') + '**. Expected: 832 programs per arm and prefix.', '',
             '| Arm | k | Verification complete / 832 | Verified | Certified strict / verified | Certified exact / verified | Audit / replay complete | Mean tokens (n) |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in summary_rows:
        def semantic_cell(field):
            number = row['certified_'+field+'_among_verified']
            value = row[field+'_percent_verified']
            suffix = f'{value:.2f}%' if value is not None else 'pending audit; confirmed count'
            return f"{number}/{row['verified_success']} ({suffix})"
        tokens = row['mean_tokens_completed']
        cost_text = f"{tokens:.1f}{' est.' if row['tokens_estimated'] else ''} ({row['cost_programs']})" if tokens is not None else 'pending'
        lines.append(f"| {row['arm']} | {row['k']} | {row['verification_complete']}/832 | {row['verified_success']} | {semantic_cell('strict')} | {semantic_cell('exact')} | {row['audit_completed']}/{row['replay_completed']} | {cost_text} |")
    lines += ['', 'Arm-specific verified subsets are descriptive, not paired. The following comparisons use identical keys in both arms.', '',
              '| k | Paired metric | Common programs | Hidden | Visible | Visible − hidden (pp) |',
              '|---:|---|---:|---:|---:|---:|']
    for row in paired_rows:
        delta = row['visible_minus_hidden_pp']
        delta_text = f'{delta:+.2f}' if delta is not None else 'pending'
        lines.append(f"| {row['k']} | {row['metric']} | {row['programs']} | {row['hidden_count']} | {row['visible_count']} | {delta_text} |")
    lines += ['', 'Verification uses the common completed set. Joint verified-and-property metrics use the common audit/replay-completed set; property-only metrics use the common verified-and-audited set. Missing programs and unresolved proofs are not silently treated as negative semantic findings.', '',
              'Generation settings, source/template/code hashes, actual saved messages, response order, and proof provenance are checked. The only intended algorithmic difference is target visibility in generation; filtering remains target-free. Independent service executions remain stochastic.', '',
              'The replay gate requires the same semantic engine, audit driver, 15-second exactness search budget, and 8-second replay timeout. No longer-timeout or explicit-predecessor addenda from the previous audit are substituted.', '',
              'Costs use accepted batch usage; @1 is estimated as full input plus output/4. Prefix costs overlap and must not be summed. See result.json and per_program.csv for missing usage and partial results.', '']
    write_json(out/'result.json', result)
    write_csv(out/'summary.csv', summary_rows)
    write_csv(out/'paired.csv', paired_rows)
    write_csv(out/'per_program.csv', rows['hidden'] + rows['visible'])
    write(out/'REPORT.md', '\n'.join(lines))
    write_json(out/'input_manifest.json', {'files': READS, 'comparison_script_sha256': sha(Path(__file__).read_bytes()),
                                         'read_only_inputs': True, 'alignment': alignment})
    print(json.dumps({'out': str(out), 'full_benchmark_complete': complete,
                      'verification_complete': {arm: {k: value['verification_complete'] for k, value in data['by_prefix'].items()}
                                                for arm, data in arms.items()}}, indent=2))


if __name__ == '__main__':
    main()
