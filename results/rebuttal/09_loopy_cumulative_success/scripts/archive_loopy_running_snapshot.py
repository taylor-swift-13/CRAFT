"""Preserve a bounded snapshot of the still-running offline Loopy rescore."""
import hashlib
import json
import shutil
from pathlib import Path
from datetime import datetime, timezone
import archive_all_rebuttal_artifacts as common
from archive_visibility_comparison import Archive, manifest_for

def main():
    R=common.R;W=common.W;src=common.RUNS/'loopy_any_iteration_20261110'
    dest=R/'09_loopy_cumulative_success';assert not dest.exists()
    stage=R/'.09_loopy_cumulative_success.snapshot_staging';assert not stage.exists();stage.mkdir()
    captured=Path('/home/yangfp/craft_empirical_runs/loopy_cumulative_frozen_snapshot_20261110')
    assert not captured.exists();captured.mkdir()
    inventory=[];logs=set();complete=0
    started=datetime.now(timezone.utc).isoformat()
    def copy(p,rel,data=None):
        b=p.read_bytes() if data is None else data;q=captured/rel;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(b)
        inventory.append({'source':str(p),'snapshot':str(rel),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
    proof_files=sorted(p for p in (src/'proofs').iterdir() if p.name.endswith(('.json','.interrupted')))
    for p in proof_files:
        if not p.is_file():continue
        b=p.read_bytes()
        try:d=json.loads(b)
        except (ValueError,UnicodeError):d={}
        # An invalid/interrupted file is retained as evidence, never a successful proof.
        copy(p,Path('proofs')/p.name,b)
        if d.get('status')=='complete':
            complete+=1
            if d.get('log'):logs.add(Path(d['log']))
    for p in sorted(logs):
        assert p.is_relative_to(src/'logs') and p.is_file()
        copy(p,p.relative_to(src))
    for p in sorted((W/'outputs/loopy_any_iteration_20261110').iterdir()):
        if p.is_file() and '.tmp' not in p.name:copy(p,Path('exports')/p.name)
    for name in ('rescore_any_iteration.py','finalize_any_iteration.py','archive_loopy_running_snapshot.py',
                 'archive_all_rebuttal_artifacts.py'):
        copy(W/'work'/name,Path('scripts')/name)
    status={'state':'running_snapshot','capture_started_utc':started,
            'capture_finished_utc':datetime.now(timezone.utc).isoformat(),
            'complete_proof_records':complete,'captured_proof_files':len(proof_files),
            'captured_completed_logs':len(logs),'benchmark_complete':False,
            'note':'The producer continues running. Exported progress is a timestamped snapshot, not final success rates. In-flight proof logs and verifier scratch are excluded; every completed proof captured here has its referenced log. No generation or proof execution is performed by this script.'}
    common.dump(captured/'snapshot_status.json',status);common.dump(captured/'source_inventory.json',inventory)
    writer=Archive(stage);writer.pack(captured,'cumulative_snapshot.tar.gz')
    writer.copy(captured/'snapshot_status.json',Path('snapshot_status.json'))
    writer.copy(captured/'source_inventory.json',Path('source_inventory.json'))
    writer.copy_tree(captured/'exports',Path('exports'));writer.copy_tree(captured/'scripts',Path('scripts'))
    common.dump(stage/'provenance.json',{'snapshot':status,'copied_files':writer.copied,'bundles':writer.bundles})
    (stage/'README.md').write_text('# Loopy cumulative scoring: running snapshot\n\n'
        'This directory preserves completed intermediate proofs and their logs from the ongoing 466-program, hidden/visible, composition/repair experiment. '
        'The scoring metric is success at any step up to the call budget. This snapshot is not a completed experiment; unresolved items in exports/progress.json remain unresolved. '
        'Original generation trajectories and final-checkpoint results remain in sibling 04.\n\n'
        'snapshot_status.json records the capture interval and counts. The producer was not stopped or changed. '
        'Restore the split evidence archive with ../restore_evidence.py. Final cumulative results must be archived separately after completion.\n')
    writer.verify_sources();common.dump(stage/'manifest.json',manifest_for(stage))
    before={n:common.sha((R/n).read_bytes()) for n in ('README.md','provenance.json','manifest.json')}
    stage.rename(dest);common.register(dest,before)
    print(json.dumps(status,indent=2),flush=True)

if __name__=='__main__':main()
