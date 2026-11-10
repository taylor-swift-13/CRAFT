"""Finish the frozen Loopy snapshot, preserving interrupted gzip bytes explicitly."""
from pathlib import Path
import gzip,io,json
import archive_visibility_comparison as archive
import archive_all_rebuttal_artifacts as common

def main():
    R=common.R;W=common.W
    captured=Path('/home/yangfp/craft_empirical_runs/loopy_cumulative_frozen_snapshot_20261110')
    stage=R/'.09_loopy_cumulative_success.snapshot_staging';dest=R/'09_loopy_cumulative_success'
    assert captured.is_dir() and stage.is_dir() and not dest.exists()
    assert not any(p.is_file() for p in stage.rglob('*')), 'Unexpected partial archive files; inspect first'
    incomplete=[]
    original_scan=archive.scan
    def scan(data,name):
        try:original_scan(data,name)
        except EOFError:
            assert name.endswith('.gz') and name.startswith('logs/'),name
            original_scan(data,name+'.raw')
            prefix_bytes=0
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
                try:
                    while chunk:=f.read(65536):
                        original_scan(chunk,name+'.decoded_prefix');prefix_bytes+=len(chunk)
                except EOFError:pass
            incomplete.append({'path':name,'sha256':common.sha(data),'bytes':len(data),
                               'decoded_prefix_bytes_scanned':prefix_bytes,
                               'status':'truncated_gzip_preserved_byte_for_byte_not_a_validated_log'})
    archive.scan=scan
    writer=archive.Archive(stage);writer.pack(captured,'cumulative_snapshot.tar.gz')
    writer.copy(captured/'snapshot_status.json',Path('snapshot_status.json'))
    writer.copy(captured/'source_inventory.json',Path('source_inventory.json'))
    writer.copy_tree(captured/'exports',Path('exports'));writer.copy_tree(captured/'scripts',Path('scripts'))
    writer.copy(Path(__file__),Path('scripts')/Path(__file__).name)
    common.dump(stage/'interrupted_log_inventory.json',incomplete)
    common.dump(stage/'provenance.json',{'snapshot':json.loads((captured/'snapshot_status.json').read_text()),
        'copied_files':writer.copied,'bundles':writer.bundles,'interrupted_logs_preserved':len(incomplete)})
    (stage/'README.md').write_text('# Loopy cumulative scoring: running snapshot\n\n'
        'Snapshot of the ongoing 466-program hidden/visible composition/repair evaluation. '
        'Unresolved items remain unresolved; this is not a final cumulative result. '
        'Source trajectories and original checkpoint records remain in sibling 04.\n\n'
        'The snapshot preserves every captured proof record and its referenced log, including '
        'interrupted files. interrupted_log_inventory.json identifies truncated gzip logs retained '
        'byte for byte; their presence must not be interpreted as successful log validation. '
        'Original files and producer state were not changed.\n\n'
        'snapshot_status.json records the capture interval. In-flight logs outside the captured '
        'proof set and temporary verifier scratch are excluded. Restore with ../restore_evidence.py. '
        'Final cumulative results must be archived separately after completion.\n')
    writer.verify_sources();common.dump(stage/'manifest.json',archive.manifest_for(stage))
    before={n:common.sha((R/n).read_bytes()) for n in ('README.md','provenance.json','manifest.json')}
    stage.rename(dest);common.register(dest,before)
    print(json.dumps({'destination':str(dest),'interrupted_logs':len(incomplete),
        'snapshot':json.loads((dest/'snapshot_status.json').read_text())},indent=2))

if __name__=='__main__':main()
