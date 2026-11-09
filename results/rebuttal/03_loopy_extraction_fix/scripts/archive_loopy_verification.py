"""Preserve a completed local verification without editing source experiments."""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import tarfile
import time

REPO = Path('/home/yangfp/CRAFT')
RUN = Path('/tmp/craft_loopy_cached_local_verify_20261107')
OUT = Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20/outputs/loopy_local_verify')


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    while True:
        result = json.loads((OUT / 'result.json').read_text())
        if result['state'] == 'complete':
            break
        if result['state'] != 'running':
            raise RuntimeError('Verification stopped without a valid complete result')
        time.sleep(10)
    manifest = json.loads((RUN / 'manifest.json').read_text())
    records = [json.loads(p.read_text()) for p in (RUN / 'tasks').glob('*.json')]
    if len(records) != 832 or any(r['status'] != 'complete' for r in records):
        raise RuntimeError('Incomplete task records')
    last_task = max(datetime.fromisoformat(r['updated_utc']) for r in records)
    while True:
        integrity = json.loads((RUN / 'input_integrity.json').read_text())
        if datetime.fromisoformat(integrity['checked_utc']) >= last_task:
            if not integrity['unchanged']:
                raise RuntimeError('Final runner input integrity check failed')
            break
        time.sleep(2)
    changed = [p for p, h in manifest['input_files'].items() if sha(Path(p)) != h]
    changed_sources = [p for p, h in manifest['source_hashes'].items() if sha(REPO / p) != h]
    if changed or changed_sources:
        raise RuntimeError('Input or verification source changed')
    destination = OUT / 'verification_evidence.tar.gz'
    temporary = OUT / 'verification_evidence.tar.gz.tmp'
    if destination.exists():
        raise RuntimeError('Refusing to overwrite an existing completed archive')
    additions = {}
    for name in ['manifest.json', 'driver.py', 'summary.json', 'input_integrity.json']:
        path = RUN / name
        if path.exists():
            additions['verification/' + name] = path
    for directory in ['tasks', 'logs', 'errors']:
        for path in sorted((RUN / directory).glob('*')):
            if path.is_file() and not path.name.endswith('.tmp'):
                additions['verification/' + directory + '/' + path.name] = path
    for path in manifest['input_files']:
        source = Path(path)
        additions['inputs/' + str(source.relative_to(REPO))] = source
    for relative in manifest['source_hashes']:
        additions['verification_sources/' + relative] = REPO / relative
    legacy = Path('/tmp/craft_loopy_extraction_replay_20261107')
    for row in records:
        imported = row.get('import_audit', {})
        if imported.get('imported', 0):
            for field in ['task_path', 'log_path']:
                source = Path(imported[field])
                expected = imported[field.replace('_path', '_sha256')]
                if sha(source) != expected:
                    raise RuntimeError('Imported proof evidence changed')
                additions['legacy_initial/' + str(source.relative_to(legacy))] = source
    for name in ['manifest.json', 'environment.json']:
        if (legacy / name).exists():
            additions['legacy_initial/' + name] = legacy / name
    legacy_driver = Path(__file__).with_name('loopy_extraction_replay.py')
    if legacy_driver.exists():
        additions['legacy_initial/driver.py'] = legacy_driver
    for name in ['result.json', 'report.txt', 'per_program.csv', 'protocol_limitations.txt',
                 'integrity_after_push.json']:
        if (OUT / name).exists():
            additions['summary/' + name] = OUT / name
    with tarfile.open(temporary, 'w:gz', compresslevel=3) as archive:
        for relative, path in sorted(additions.items()):
            archive.add(path, arcname=relative, recursive=False)
    temporary.replace(destination)
    h = sha(destination)
    (OUT / 'verification_evidence.sha256').write_text(h + '  ' + destination.name + '\n')
    audit = dict(complete=True, archived_files=len(additions), archive=str(destination),
                 archive_bytes=destination.stat().st_size, sha256=h,
                 original_inputs_checked=len(manifest['input_files']), changed_original_inputs=[],
                 changed_verification_sources=[], imported_evidence_preserved=True)
    (OUT / 'archive_integrity.json').write_text(json.dumps(audit, indent=2) + '\n')
    print(json.dumps(audit), flush=True)


if __name__ == '__main__':
    main()
