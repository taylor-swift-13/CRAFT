"""Archive remaining experiment artifacts, without rerunning any experiment."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import os
from datetime import datetime, timezone
import importlib.util

W = Path(__file__).resolve().parent.parent
R = Path('/home/yangfp/CRAFT/results/rebuttal')
RUNS = Path('/home/yangfp/craft_empirical_runs')
spec = importlib.util.spec_from_file_location('visibility_archive', W/'work/archive_visibility_comparison.py')
lib = importlib.util.module_from_spec(spec); spec.loader.exec_module(lib)

def sha(data): return hashlib.sha256(data).hexdigest()
def file_sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while b:=f.read(1024*1024):h.update(b)
    return h.hexdigest()
def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')

def known_content():
    known = {}
    root = json.loads((R/'manifest.json').read_text())
    for row in root['files']:
        p = R/row['path']
        assert file_sha(p) == row['sha256'], str(p)
        known.setdefault(row['sha256'], row['path'])
    for p in R.glob('*/evidence/*.index.json'):
        d=json.loads(p.read_text()); whole=hashlib.sha256()
        for part in d['parts']:
            pp=p.parent/part['file'];assert pp.stat().st_size==part['bytes'] and file_sha(pp)==part['sha256']
            with pp.open('rb') as f:
                while b:=f.read(1024*1024):whole.update(b)
        assert whole.hexdigest()==d['sha256']
        known.setdefault(d['sha256'], str(p.relative_to(R))+'#whole-archive')
        for row in d['members']:
            known.setdefault(row['sha256'], str(p.relative_to(R))+'#'+row['path'])
    return known

def scan(data, name):
    lib.scan(data, name)
    if name.endswith(('.tar.gz','.tgz')):
        with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as tf:
            for m in tf:
                assert not m.issym() and not m.islnk(), m.name
                assert '..' not in Path(m.name).parts and not Path(m.name).is_absolute()
                if m.isfile(): scan(tf.extractfile(m).read(),m.name)

def files_under(root, label, excluded):
    if not root.exists(): return []
    result=[]
    for directory,dirs,names in os.walk(root):
        dirs[:]=sorted(d for d in dirs if d not in ('scratch','__pycache__'))
        for name in sorted(names):
            p=Path(directory)/name;rel=p.relative_to(root)
            assert not p.is_symlink(), str(p)
            name=str(Path(label)/rel)
            if excluded(p,rel):continue
            result.append((p,name))
    return result

def scan_stream(stream,name):
    assert not Path(name).is_absolute() and '..' not in Path(name).parts,name
    assert not any(x=='.env' or x.startswith('.env.') for x in Path(name).parts),name
    if name.endswith(('.tar.gz','.tgz')):
        with tarfile.open(fileobj=stream,mode='r|gz') as tf:
            for m in tf:
                assert not m.issym() and not m.islnk(),m.name
                if m.isfile():scan_stream(tf.extractfile(m),m.name)
    elif name.endswith('.gz'):
        with gzip.GzipFile(fileobj=stream,mode='rb') as gz:scan_stream(gz,name[:-3])
    else:
        tail=b''
        while chunk:=stream.read(1024*1024):
            block=tail+chunk
            assert not any(p.search(block) for p in lib.SECRET_PATTERNS),'Possible credential: '+name
            tail=block[-512:]

def sources(mode):
    if mode=='cumulative':
        out=W/'outputs/loopy_any_iteration_20261110'
        result=json.loads((out/'result.json').read_text())
        assert result['status']=='complete' and result['unresolved_total']==0
        assert result['new_model_calls']==0 and result['changed_frozen_inputs']==[]
        audit=json.loads((out/'audit.json').read_text())
        assert audit['status']=='pass' and audit['all_cumulative_counts_reconstructed'] is True
        status=json.loads((out/'finalizer_status.json').read_text())
        assert status['state'] in ('complete','draft_ready_main_section_changed')
        rows=files_under(out,'exports',lambda p,r:False)
        rows+=files_under(RUNS/'loopy_any_iteration_20261110','run',lambda p,r:False)
        for n in ('rescore_any_iteration.py','finalize_any_iteration.py'):
            rows.append((W/'work'/n,'scripts/'+n))
        return rows
    skip_dirs={'rebuttal_comments','loopy_any_iteration_20261110','craft_visibility_recovery_20261110'}
    skip_names={'commit_subject_correction.json','table_projection_evidence.txt','rebuttal_archive_push.json',
                'rebuttal_archive_validation.json'}
    def excluded(p,r):
        return r.parts[0] in skip_dirs or p.name in skip_names or p.name.startswith('rebuttal_')
    rows=files_under(W/'outputs','workspace_outputs',excluded)
    skip_scripts={'correct_commit_subjects.py','organize_completed_rebuttal_20261110.py','update_rebuttal_question_titles.py'}
    for p in sorted((W/'work').iterdir()):
        if p.is_file() and p.suffix in ('.py','.json') and p.name not in skip_scripts:
            rows.append((p,'scripts_and_audits/'+p.name))
    for root in sorted(RUNS.iterdir()):
        if root.is_dir() and root.name not in ('loopy_any_iteration_20261110','recovered_hidden_evidence_20261110'):
            rows+=files_under(root,'persistent_runs/'+root.name,lambda p,r:False)
    return rows

def register(dest,before):
    for name,h in before.items():assert sha((R/name).read_bytes())==h,'Concurrent index edit'
    text=(R/'README.md').read_text();lines=text.splitlines()
    title='Cumulative success at every iteration' if dest.name.startswith('09') else 'Additional pilots, probes, diagnostics and complete artifact coverage'
    idx=max(i for i,l in enumerate(lines) if l.startswith('| ['))
    lines.insert(idx+1,f'| [{dest.name}]({dest.name}/) | {title} | Rebuttal supporting evidence |')
    if dest.name.startswith('10'):
        lines+=['','The supplemental archive 10 additionally preserves non-final pilots, probes and diagnostics',
                'previously omitted from 01–08. Its source inventory maps every scoped file to an',
                'existing hash-identical archive member or a new supplemental member. These records',
                'are historical evidence, not replacements for final experiment results.']
    (R/'README.md').write_text('\n'.join(lines)+'\n')
    prov=json.loads((R/'provenance.json').read_text())
    prov.setdefault('extensions',[]).append({'directory':dest.name,'registered_utc':datetime.now(timezone.utc).isoformat(),
        'provenance':dest.name+'/provenance.json','sha256':sha((dest/'provenance.json').read_bytes())})
    dump(R/'provenance.json',prov)
    old=json.loads((R/'manifest.json').read_text()); entries={x['path']:x for x in old['files']}
    for p in [R/'README.md',R/'provenance.json',*sorted(dest.rglob('*'))]:
        if p.is_file():
            b=p.read_bytes();rel=str(p.relative_to(R));entries[rel]={'path':rel,'bytes':len(b),'sha256':sha(b)}
    rows=[entries[k] for k in sorted(entries)]
    dump(R/'manifest.json',{'files':rows,'file_count':len(rows),'total_bytes':sum(x['bytes'] for x in rows),'hash':'sha256'})

def main():
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['supplemental','cumulative']);args=ap.parse_args()
    name='09_loopy_cumulative_success' if args.mode=='cumulative' else '10_additional_artifacts_and_coverage'
    dest=R/name; assert not dest.exists(),'Destination already exists; do not overwrite'
    before={n:sha((R/n).read_bytes()) for n in ('README.md','provenance.json','manifest.json')}
    known=known_content();print('Existing archive hashes verified',flush=True)
    rows=sources(args.mode);print('Source files',len(rows),flush=True);inventory=[];unique=[]
    for pos,(p,rel) in enumerate(rows):
        h=file_sha(p);size=p.stat().st_size
        row={'source':str(p),'bytes':size,'sha256':h}
        if h in known:row['stored_at']=known[h]
        else:
            with p.open('rb') as f:scan_stream(f,rel)
            loc=name+'/evidence/additional.tar.gz.index.json#'+rel
            row['stored_at']=loc;known[h]=loc;unique.append((p,rel,h,size))
        inventory.append(row)
        if pos%5000==0:print('Inventoried',pos,flush=True)
    print(json.dumps({'sources':len(rows),'unique_new':len(unique),'new_bytes':sum(x[3] for x in unique)}),flush=True)
    stage=R/('.'+name+'.staging');assert not stage.exists();stage.mkdir()
    ev=stage/'evidence';ev.mkdir();members=[]
    with tempfile.TemporaryFile() as temp:
        with gzip.GzipFile(fileobj=temp,mode='wb',mtime=0,compresslevel=6) as gz:
            with tarfile.open(fileobj=gz,mode='w|') as tf:
                for p,rel,h,size in unique:
                    assert file_sha(p)==h,'Source changed: '+str(p)
                    info=tarfile.TarInfo(rel);info.size=size;info.mode=0o644
                    with p.open('rb') as f:tf.addfile(info,f)
                    members.append({'path':rel,'bytes':size,'sha256':h})
        temp.seek(0);parts=[];whole=hashlib.sha256()
        while b:=temp.read(24*1024*1024):
            part='additional.tar.gz.part'+str(len(parts)).zfill(3);(ev/part).write_bytes(b);whole.update(b)
            parts.append({'file':part,'bytes':len(b),'sha256':sha(b)})
    dump(ev/'additional.tar.gz.index.json',{'archive':'additional.tar.gz','source':'Explicit source_inventory.json',
         'file_count':len(members),'bytes':sum(x['bytes'] for x in parts),'sha256':whole.hexdigest(),'parts':parts,'members':members})
    for row in inventory:assert file_sha(Path(row['source']))==row['sha256'],'Source drift: '+row['source']
    dump(stage/'source_inventory.json',{'captured_utc':datetime.now(timezone.utc).isoformat(),'mode':args.mode,
        'sources':inventory,'source_count':len(rows),'new_unique_files':len(unique),'uncovered_files':0,
        'excluded':'Temporary verifier scratch/Python caches, rebuttal prose editing, Git-history administration, failed recovery tar; unrelated original chapter runs. Loopy cumulative artifacts are in 09; recovered duplicates remain in 01.'})
    dump(stage/'provenance.json',{'mode':args.mode,'source_inventory':'source_inventory.json','no_generation_or_verification':True,
        'prior_hash_identical_files_referenced':len(rows)-len(unique),'new_unique_files':len(unique)})
    (stage/'README.md').write_text('# '+name+'\n\n'+
        ('Completed cumulative scoring on 466 Loopy programs, targets hidden/visible, compose/repair, budgets 1/4/8. All intermediate proof evidence and exported counts are preserved. No new model calls.\n' if args.mode=='cumulative' else
         'Supplemental experimental artifacts, including superseded pilots, connectivity probes, semantic development outputs and configuration diagnostics. They retain their original status and must not be substituted for final results.\n')+
        '\nsource_inventory.json maps every scoped input to a hash-identical existing archived file/member or a new member. Restore split bundles with ../restore_evidence.py. Only new distinct contents are stored; repeated contents retain all source mappings. Raw experiments are unchanged.\n')
    dump(stage/'manifest.json',lib.manifest_for(stage));stage.rename(dest);register(dest,before)
    print('ARCHIVED',dest,flush=True)

if __name__=='__main__':main()
