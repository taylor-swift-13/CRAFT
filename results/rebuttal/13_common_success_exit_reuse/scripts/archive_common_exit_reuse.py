"""Archive the complete offline probe without touching original experiments."""
import hashlib, json, re, shutil, subprocess, tarfile
from pathlib import Path

W=Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20')
SRC=W/'outputs/common_success_exit_reuse_final_20261112'
REPO=Path('/home/yangfp/CRAFT');BASE=REPO/'results/rebuttal';DST=BASE/'13_common_success_exit_reuse'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def main():
    assert not DST.exists()
    validation=json.loads((SRC/'validation.json').read_text());assert not validation['contradictions']
    assert (SRC/'REPORT.md').exists() and (SRC/'README.md').exists()
    clean=subprocess.check_output(['git','-C',str(REPO),'status','--porcelain','--untracked-files=no'],text=True)
    assert not clean.strip(),'Tracked worktree changed; inspect before adding archive indexes.'
    prior={n:sha(BASE/n) for n in ['README.md','manifest.json','provenance.json']}
    copied=[]
    for src in sorted(SRC.rglob('*')):
        if not src.is_file():continue
        dst=DST/src.relative_to(SRC);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst);dst.chmod(0o644)
        digest=sha(src);assert digest==sha(dst)
        # No credentials or configuration are part of these experiment records.
        assert not re.search(rb'(?:sk-[A-Za-z0-9_-]{20,}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----)',src.read_bytes()),str(src)
        copied.append({'source':str(src),'destination':str(dst.relative_to(BASE)),'bytes':dst.stat().st_size,'sha256':digest})
    print('COPIED',len(copied),'final files',flush=True)
    scripts=['inventory_common_exit_reuse.py','common_exit_goal_templates.py','run_common_exit_reuse.py','run_common_exit_reuse_v2.py',
      'build_common_exit_reuse_final.py','audit_common_exit_reuse_final.py','write_common_exit_reuse_report.py',
      'replay_common_exit_reuse.py','archive_common_exit_reuse.py','compare_exit_reuse_goals.py','analyze_craft16_semantics.py']
    (DST/'scripts').mkdir()
    for name in scripts:
        src=W/'work'/name;dst=DST/'scripts'/name;shutil.copyfile(src,dst);dst.chmod(0o644)
        copied.append({'source':str(src),'destination':str(dst.relative_to(BASE)),'bytes':dst.stat().st_size,'sha256':sha(dst)})
    (DST/'history').mkdir();bundles=[]
    for label,dirname in [('non_contextual','common_success_exit_reuse_20261112'),('exit_context','common_success_exit_reuse_20261112_v2')]:
        root=W/'outputs'/dirname;tarpath=DST/'history'/f'{label}.tar.gz';members=[]
        with tarfile.open(tarpath,'w:gz',compresslevel=6) as tf:
            for src in sorted(root.rglob('*')):
                if not src.is_file():continue
                name='history/'+label+'/'+str(src.relative_to(root))
                tf.add(src,arcname=name,recursive=False)
                members.append({'source':str(src),'name':name,'bytes':src.stat().st_size,'sha256':sha(src)})
        index={'archive':str(tarpath.relative_to(DST)),'sha256':sha(tarpath),'bytes':tarpath.stat().st_size,'file_count':len(members),'members':members}
        write(tarpath.with_suffix(tarpath.suffix+'.index.json'),index)
        bundles.append({'directory':DST.name,'archive':str(tarpath.relative_to(BASE)),'sha256':index['sha256'],'bytes':index['bytes'],'file_count':len(members)})
        print('ARCHIVED HISTORY',label,len(members),flush=True)
    (DST/'history/README.md').write_text('''# Complete screening histories

The two archives retain every candidate pool, screening decision, solver record,
and preliminary summary from the non-contextual and exit-context stages. The
final probe retains all stage-1 goals and only uses exit-context goals to fill
remaining slots. Preliminary summaries are not the final reported comparison.

To restore either archive from the experiment root:

```sh
tar -xzf history/non_contextual.tar.gz
tar -xzf history/exit_context.tar.gz
```

Each adjacent index lists original source paths, member names, sizes, and hashes.
''')
    for n,digest in prior.items():assert sha(BASE/n)==digest,'Concurrent archive-index change: '+n
    readme=(BASE/'README.md').read_text();lines=readme.splitlines();matches=[i for i,l in enumerate(lines) if l.startswith('| [12_reuse_exit_goal_comparison]')]
    assert len(matches)==1
    lines.insert(matches[0]+1,'| [13_common_success_exit_reuse](13_common_success_exit_reuse/) | All 578 jointly verified programs; symmetric source-template exit-goal probe with full coverage accounting | Multi-goal reuse analysis |')
    (BASE/'README.md').write_text('\n'.join(lines)+'\n')
    prov=json.loads((BASE/'provenance.json').read_text());prov['copied_files'].extend(copied);prov['bundles'].extend(bundles)
    prov['extensions'].append({'archived_on':'2026-11-12','directory':DST.name,'description':'All originally successful pairs, fixed source-template pools, symmetric correctness curation, offline paired exit-goal checks, complete unknown and missing-slot accounting.','copied_file_count':len(copied),'historical_bundles':len(bundles),'original_inputs_unchanged':True})
    write(BASE/'provenance.json',prov)
    manifest=json.loads((BASE/'manifest.json').read_text());entries={x['path']:x for x in manifest['files']}
    for p in list(DST.rglob('*'))+[BASE/'README.md',BASE/'provenance.json']:
        if p.is_file():entries[str(p.relative_to(BASE))]={'path':str(p.relative_to(BASE)),'bytes':p.stat().st_size,'sha256':sha(p)}
    manifest['files']=sorted(entries.values(),key=lambda x:x['path']);manifest['file_count']=len(entries);manifest['total_bytes']=sum(x['bytes'] for x in entries.values());write(BASE/'manifest.json',manifest)
    print(json.dumps({'destination':str(DST),'final_files':len(copied),'history_bundles':bundles},indent=2),flush=True)

if __name__=='__main__':main()
