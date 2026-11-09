"""Restore a split evidence archive after verifying all part/member hashes."""
import argparse
import hashlib
import json
import tarfile
import tempfile
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('index', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    meta = json.loads(args.index.read_text())
    target = args.out.resolve()
    target.mkdir(parents=True, exist_ok=True)
    if any(target.iterdir()):
        raise SystemExit('Output directory must be empty')
    expected = {row['path']: row for row in meta['members']}
    with tempfile.TemporaryFile() as stream:
        whole = hashlib.sha256()
        for part in meta['parts']:
            if Path(part['file']).name != part['file']:
                raise ValueError('Invalid part name')
            data = (args.index.parent / part['file']).read_bytes()
            if len(data) != part['bytes'] or sha(data) != part['sha256']:
                raise ValueError('Part checksum mismatch: ' + part['file'])
            stream.write(data)
            whole.update(data)
        if whole.hexdigest() != meta['sha256']:
            raise ValueError('Archive checksum mismatch')
        stream.seek(0)
        count = 0
        with tarfile.open(fileobj=stream, mode='r:gz') as tf:
            for member in tf:
                rel = Path(member.name)
                if rel.is_absolute() or '..' in rel.parts or member.issym() or member.islnk():
                    raise ValueError('Unsafe member: ' + member.name)
                if member.isdir():
                    continue
                if not member.isfile() or member.name not in expected:
                    raise ValueError('Unexpected member: ' + member.name)
                data = tf.extractfile(member).read()
                record = expected[member.name]
                if len(data) != record['bytes'] or sha(data) != record['sha256']:
                    raise ValueError('Member checksum mismatch: ' + member.name)
                dest = target / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                count += 1
        if count != len(expected):
            raise ValueError('Missing members')
    print(f'Restored {count} verified files to {target}')


if __name__ == '__main__':
    main()
