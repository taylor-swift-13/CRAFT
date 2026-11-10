"""Archive the completed target-visibility comparison; never generate or verify.

Default gates require both arms, all four prefixes, and all 832 programs to have
completed verification, semantic analysis, replay, and comparison reporting.
Run --check-only for a read-only readiness check. The destination is new-only.
--update-root-index appends registration after successful archive validation;
it never edits original chapter-organized results or prior experiment folders.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile

REPO = Path('/home/yangfp/CRAFT')
WORKSPACE = Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20')
ROOT = REPO/'results/rebuttal'
NAME = '08_target_visibility_matched'
PART_SIZE = 24*1024*1024
PREFIXES = [1, 4, 8, 16]
FILES = ['result.json', 'summary.csv', 'paired.csv', 'per_program.csv', 'REPORT.md', 'input_manifest.json']
SECRET_PATTERNS = [re.compile(rb'sk-[A-Za-z0-9_-]{20,}'),
                   re.compile(rb'Bearer\s+[A-Za-z0-9_.-]{24,}'),
                   re.compile(rb'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----')]


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    return json.loads(path.read_text())


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_name(path.name+f'.tmp.{os.getpid()}')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    tmp.replace(path)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def scan(data, name):
    parts=PurePosixPath(name).parts
    require(not PurePosixPath(name).is_absolute() and '..' not in parts, 'Unsafe archive path: '+name)
    require(not any(part=='.env' or part.startswith('.env.') for part in parts), 'Environment file: '+name)
    require(not any(pattern.search(data) for pattern in SECRET_PATTERNS), 'Possible credential: '+name)
    if name.endswith('.gz') and not name.endswith('.tar.gz'):
        scan(gzip.decompress(data),name[:-3])


def input_keys(manifest):
    mapping=manifest.get('original_inputs',{})
    require(isinstance(mapping,dict) and len(mapping)==832, 'Expected 832 frozen original inputs')
    result={Path(path).parent.name+'_'+Path(path).stem:checksum for path,checksum in mapping.items()}
    require(len(result)==832,'Duplicate program keys')
    return result


def check_completion(args):
    visible_manifest=load(args.run/'manifest.json')
    hidden_manifest=load(args.hidden_run/'manifest.json')
    keys=input_keys(visible_manifest)
    require(keys==input_keys(hidden_manifest),'Visible and hidden source-program hashes differ')
    require(visible_manifest.get('target_hidden') is False,'Visible generation manifest is not target-visible')
    require(hidden_manifest.get('target_hidden') is True,'Hidden control manifest is not target-hidden')
    for name in ['model','reasoning_effort','n_rollouts','request_schedule','max_completion_tokens',
                 'max_invariants_per_response','repair','wp']:
        require(visible_manifest.get(name)==hidden_manifest.get(name),'Generation/proof setting mismatch: '+name)
    require(visible_manifest.get('model')=='gpt-5-nano' and visible_manifest.get('reasoning_effort')=='minimal',
            'Expected GPT-5-nano/minimal')
    for prompt in ['generate_prompt.txt','system_prompt.txt']:
        path=str(REPO/'prompt'/prompt)
        require(visible_manifest.get('sources',{}).get(path) is not None
                and visible_manifest['sources'][path]==hidden_manifest.get('sources',{}).get(path),
                'Prompt template mismatch: '+prompt)
    counts={}
    for arm,run,prefix in [('visible',args.run,args.prefix_run),('hidden',args.hidden_run,args.hidden_prefix_run)]:
        mf=visible_manifest if arm=='visible' else hidden_manifest
        prefix_protocol=load(prefix/'protocol.json')
        require(prefix_protocol.get('source_fingerprint')==mf['fingerprint'],arm+' prefix fingerprint mismatch')
        counts[arm]={}
        for k in PREFIXES:
            completed=0
            for key,checksum in keys.items():
                source=run/'sources'/(key+'.original.c')
                require(source.is_file() and sha(source.read_bytes())==checksum,arm+' source changed: '+key)
                path=run/'verification'/(key+'.json') if k==16 else prefix/'verification'/f'{key}_k{k}.json'
                if not path.exists():
                    continue
                row=load(path)
                if (row.get('status')=='complete' and isinstance(row.get('verified'),bool)
                        and isinstance(row.get('survivors'),list) and isinstance(row.get('union'),list)):
                    require(row.get('key')==key, 'Verification key mismatch: '+str(path))
                    completed+=1
            counts[arm][str(k)]=completed
        require(all(n==832 for n in counts[arm].values()),f'{arm} verification incomplete: {counts[arm]}')
    for key in keys:
        gp=args.run/'generations'/(key+'.json')
        require(gp.is_file(),'Missing visible generation: '+key)
        row=load(gp)
        require(row.get('status')=='complete' and len(row.get('responses',[]))==16,'Incomplete visible generation: '+key)
        require(row.get('fingerprint')==visible_manifest['fingerprint'],'Visible generation fingerprint mismatch: '+key)
        record=load(args.run/'verification'/(key+'.json'))
        require(record.get('generation_sha256')==sha(gp.read_bytes()),'Visible @16 generation hash mismatch: '+key)
    audit_protocols={}
    for arm,path in [('visible',args.semantics_root),('hidden',args.control_root)]:
        summary=load(path/'summary.json')
        protocol=load(path/'protocol.json')
        audit_protocols[arm]=protocol
        expected_fingerprint=(visible_manifest if arm=='visible' else hidden_manifest)['fingerprint']
        require(protocol.get('source_fingerprint')==expected_fingerprint
                and summary.get('source_fingerprint')==expected_fingerprint,
                arm+' semantic audit belongs to a different generation pool')
        require(summary.get('full_benchmark_complete') is True,arm+' semantic audit not full-benchmark complete')
        require(summary.get('complete') is True,arm+' semantic audit not complete')
        require(summary.get('input_integrity',{}).get('changed_paths')==[],arm+' semantic inputs changed')
        require(protocol.get('prefixes')==PREFIXES and set(protocol.get('selected_keys',[]))==set(keys),arm+' audit population mismatch')
        for k in PREFIXES:
            row=summary.get('by_prefix',{}).get(str(k),{})
            require(row.get('full_benchmark_complete') is True and row.get('analysis_completed')==832
                    and row.get('replay_pending')==0,arm+f' k{k} semantic audit incomplete')
            for key in keys:
                for folder in ['cases','proof_queries','replay']:
                    # Audit driver evidence_paths defines replay/ (singular).
                    require((path/f'k{k}'/folder/(key+'.json')).is_file(),
                            f'Missing {arm} k{k} {folder}: {key}')
    for field in ['analysis_engine_sha256','driver_sha256','exact_budget_seconds','replay_timeout_ms']:
        require(audit_protocols['visible'].get(field)==audit_protocols['hidden'].get(field),
                'Audit setting mismatch: '+field)
    for name in FILES:
        require((args.comparison/name).is_file(),'Missing comparison report: '+name)
    comparison=load(args.comparison/'result.json')
    require(comparison.get('full_benchmark_complete') is True,'Comparison report is incomplete')
    alignment=comparison.get('alignment',{})
    for field in ['analysis_engine_sha256','driver_sha256']:
        require(alignment.get(field)==audit_protocols['visible'].get(field) and alignment.get(field),
                'Comparison audit alignment mismatch: '+field)
    require(alignment.get('exact_budget_seconds')==15 and alignment.get('replay_timeout_ms')==8000,
            'Comparison semantic budgets differ from the common protocol')
    input_manifest=load(args.comparison/'input_manifest.json')
    require(input_manifest.get('comparison_script_sha256') == sha((WORKSPACE/'work/compare_visible_hidden.py').read_bytes()),
            'Comparison script changed after the report was generated')
    require(input_manifest.get('read_only_inputs') is True and isinstance(input_manifest.get('files'),dict),
            'Comparison lacks a read-only input hash manifest')
    for source,checksum in input_manifest['files'].items():
        path=Path(source)
        require(path.is_file() and sha(path.read_bytes())==checksum,'Comparison input changed: '+source)
    for arm in ['hidden','visible']:
        for k in PREFIXES:
            row=comparison.get('arms',{}).get(arm,{}).get('by_prefix',{}).get(str(k),{})
            require(row.get('expected_programs')==832 and row.get('full_benchmark_complete') is True,
                    f'Comparison population incomplete: {arm} k{k}')
            for field in ['verification_complete','audit_completed','replay_completed']:
                require(row.get(field)==832,f'Comparison {field} incomplete: {arm} k{k}')
    return dict(checked_utc=now(),expected_programs=832,prefixes=PREFIXES,
                verification_completed=counts,full_benchmark_complete=True,
                visible_fingerprint=visible_manifest['fingerprint'],hidden_fingerprint=hidden_manifest['fingerprint'],
                comparison_sha256=sha((args.comparison/'result.json').read_bytes()),
                note='Unknown semantic outcomes may be completed but are not certified; completion does not imply every property was proved.')


class Archive:
    def __init__(self,destination):
        self.destination=destination
        self.copied=[]
        self.bundles=[]
        self.frozen={}

    def copy(self,source,relative):
        require(source.is_file() and not source.is_symlink(),'Missing or linked source: '+str(source))
        data=source.read_bytes();scan(data,str(relative))
        dest=self.destination/relative;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
        item=dict(source=str(source),destination=str(relative),bytes=len(data),sha256=sha(data))
        self.copied.append(item);self.frozen[str(source)]=item['sha256']

    def copy_tree(self,source,relative):
        require(source.is_dir(),'Missing source directory: '+str(source))
        for path in sorted(source.rglob('*')):
            require(not path.is_symlink(),'Linked input: '+str(path))
            if path.is_file():self.copy(path,Path(relative)/path.relative_to(source))

    def pack(self,source,name,exclude_dirs=('scratch','__pycache__')):
        members=[]
        evidence=self.destination/'evidence';evidence.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix='.tar.gz') as temp:
            with open(temp.name,'wb') as stream:
                with gzip.GzipFile(fileobj=stream,mode='wb',mtime=0,compresslevel=6) as zipped:
                    with tarfile.open(fileobj=zipped,mode='w|') as archive:
                        for path in sorted(source.rglob('*')):
                            relative=path.relative_to(source)
                            if any(part in exclude_dirs for part in relative.parts):continue
                            require(not path.is_symlink(),'Linked archive input: '+str(path))
                            if not path.is_file():continue
                            data=path.read_bytes();scan(data,str(relative))
                            checksum=sha(data);self.frozen[str(path)]=checksum
                            members.append(dict(path=str(relative),bytes=len(data),sha256=checksum))
                            info=archive.gettarinfo(str(path),arcname=str(relative))
                            info.uid=info.gid=0;info.uname=info.gname='';info.mtime=0
                            import io
                            archive.addfile(info,io.BytesIO(data))
            whole=hashlib.sha256();parts=[]
            with open(temp.name,'rb') as stream:
                while data:=stream.read(PART_SIZE):
                    filename=name+f'.part{len(parts):03d}'
                    (evidence/filename).write_bytes(data);whole.update(data)
                    parts.append(dict(file=filename,bytes=len(data),sha256=sha(data)))
            info=dict(archive=name,source=str(source),bytes=Path(temp.name).stat().st_size,
                      sha256=whole.hexdigest(),parts=parts,file_count=len(members),members=members)
            dump(evidence/(name+'.index.json'),info)
            self.bundles.append(dict(index='evidence/'+name+'.index.json',source=str(source),
                                     sha256=whole.hexdigest(),file_count=len(members)))
            print('BUNDLE',name,len(members),'files',flush=True)

    def verify_sources(self):
        for path,checksum in self.frozen.items():
            require(Path(path).is_file() and sha(Path(path).read_bytes())==checksum,
                    'Source changed during archiving: '+path)


def manifest_for(directory):
    files=[]
    for path in sorted(directory.rglob('*')):
        if path.is_file() and path!=directory/'manifest.json':
            data=path.read_bytes();files.append(dict(path=str(path.relative_to(directory)),bytes=len(data),sha256=sha(data)))
    return dict(files=files,total_bytes=sum(item['bytes'] for item in files),file_count=len(files),hash='sha256')


def register_root(destination,root_before):
    """Append one extension, retaining all pre-existing entries and checking drift."""
    for name,checksum in root_before.items():
        require(sha((ROOT/name).read_bytes())==checksum,'Root index changed during archive: '+name)
    readme=(ROOT/'README.md').read_text()
    row=f'| [{NAME}]({NAME}/) | Matched target-visible/hidden generation and prefix semantic audits | R1/R2 reuse and visibility analysis |'
    require(NAME not in readme,'Archive already registered in root README')
    lines=readme.splitlines()
    table_rows=[i for i,line in enumerate(lines) if line.startswith('| [')]
    require(table_rows,'Cannot locate root experiment table')
    lines.insert(table_rows[-1]+1,row)
    provenance=load(ROOT/'provenance.json')
    extensions=provenance.setdefault('extensions',[])
    require(not any(item.get('directory')==NAME for item in extensions),'Duplicate provenance extension')
    extensions.append(dict(directory=NAME,registered_utc=now(),provenance=NAME+'/provenance.json',
                           sha256=sha((destination/'provenance.json').read_bytes())))
    old=load(ROOT/'manifest.json');entries={item['path']:item for item in old['files']}
    for item in old['files']:
        path=ROOT/item['path']
        require(path.is_file() and sha(path.read_bytes())==item['sha256'],'Existing archived file changed: '+item['path'])
    (ROOT/'README.md').write_text('\n'.join(lines)+'\n')
    dump(ROOT/'provenance.json',provenance)
    for name in ['README.md','provenance.json']:
        data=(ROOT/name).read_bytes();entries[name]=dict(path=name,bytes=len(data),sha256=sha(data))
    for path in sorted(destination.rglob('*')):
        if path.is_file():
            relative=str(path.relative_to(ROOT));require(relative not in entries,'Existing root manifest entry: '+relative)
            data=path.read_bytes();entries[relative]=dict(path=relative,bytes=len(data),sha256=sha(data))
    ordered=[entries[name] for name in sorted(entries)]
    dump(ROOT/'manifest.json',dict(files=ordered,total_bytes=sum(x['bytes'] for x in ordered),
                                   file_count=len(ordered),hash='sha256'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    base=Path('/home/yangfp/craft_empirical_runs')
    parser.add_argument('--run',type=Path,default=base/'craft16_target_visible_20261110')
    parser.add_argument('--prefix-run',type=Path,default=base/'craft16_target_visible_prefixes_20261110')
    parser.add_argument('--semantics-root',type=Path,default=base/'craft16_target_visible_semantics_20261110')
    parser.add_argument('--control-root',type=Path,default=base/'craft16_hidden_prefix_semantics_control_20261110')
    parser.add_argument('--hidden-run',type=Path,default=Path('/tmp/craft16_minimal_20261107_v2'))
    parser.add_argument('--hidden-prefix-run',type=Path,default=base/'craft16_prefix_trend_20261108')
    parser.add_argument('--comparison',type=Path,default=WORKSPACE/'outputs/craft_visibility_comparison_20261110')
    parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--update-root-index',action='store_true',help='Append README/provenance/manifest registration after successful archive; review before committing.')
    args=parser.parse_args()
    gate=check_completion(args)
    if args.check_only:
        print(json.dumps(gate,indent=2));return
    require(ROOT.is_dir(),'Parent rebuttal archive is absent')
    destination=ROOT/NAME
    require(not destination.exists(),'Destination already exists; inspect instead of overwriting: '+str(destination))
    root_before={name:sha((ROOT/name).read_bytes()) for name in ['README.md','provenance.json','manifest.json']}
    staging=ROOT/(f'.{NAME}.staging-{os.getpid()}')
    require(not staging.exists(),'Staging directory already exists')
    staging.mkdir()
    writer=Archive(staging)
    try:
        writer.copy_tree(args.comparison,Path('comparison'))
        for label,source in [('visible_generation',args.run),('visible_prefixes',args.prefix_run),
                             ('visible_semantic_audit',args.semantics_root),('hidden_semantic_control',args.control_root)]:
            writer.pack(source,label+'.tar.gz')
            for path in sorted(source.glob('*.json')):
                writer.copy(path,Path('summaries')/label/path.name)
            for path in sorted(source.glob('k*/summary.json')):
                writer.copy(path,Path('summaries')/label/path.parent.name/path.name)
        for folder in ['craft16_target_visible_20261110','craft16_target_visible_prefixes_20261110',
                       'craft_visibility_orchestration_20261110']:
            source=WORKSPACE/'outputs'/folder
            if source.is_dir():writer.copy_tree(source,Path('exports')/folder)
        pilot=Path('/home/yangfp/craft_empirical_runs/craft16_target_visible_semantics_pilot_20261110')
        if pilot.is_dir():writer.pack(pilot,'visible_semantic_pilot.tar.gz')
        late=Path('/home/yangfp/craft_empirical_runs/craft16_target_visible_late_prefix_NLA_lipus_7_20261110')
        if late.is_dir():writer.pack(late,'visible_late_prefix.tar.gz')
        scripts=['craft16_target_visible.py','craft_visible_prefixes.py','audit_visible_prefix_semantics.py',
                 'analyze_craft16_semantics.py','export_craft_visible.py','resume_craft_visible.py',
                 'run_visible_evaluation_pipeline.py','verify_visible_nonpilot.py','compare_visible_hidden.py',
                 'archive_visibility_comparison.py','complete_visible_late_prefix.py',
                 'build_visibility_chinese_report.py','summarize_visibility_timing.py']
        for name in scripts:
            writer.copy(WORKSPACE/'work'/name,Path('scripts')/name)
        frozen_sources=load(args.run/'manifest.json').get('sources',{})
        for name in ['generate_prompt.txt','system_prompt.txt','naive_prompt.txt']:
            source=REPO/'prompt'/name
            expected=frozen_sources.get(str(source))
            require(expected and source.is_file() and sha(source.read_bytes())==expected,
                    'Prompt source differs from visible manifest: '+str(source))
            writer.copy(source,Path('scripts/prompts')/name)
            require(writer.copied[-1]['sha256']==expected,'Prompt changed during copying: '+str(source))
        # Include any final comparison generator declared by its agent without changing historical scripts.
        for path in sorted((WORKSPACE/'work').glob('*visibility*comparison*.py')):
            if path.name not in scripts:writer.copy(path,Path('scripts')/path.name)
        dump(staging/'completion_gate.json',gate)
        head=subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip()
        dump(staging/'provenance.json',dict(archived_on=now(),repository_head_before_archive=head,
             copied_files=writer.copied,bundles=writer.bundles,
             scope='Completed target-visible generation and same-pool prefixes, common-engine semantic audits of both arms, and their paired comparison. Historical hidden generation evidence remains in sibling 01/02; old experiments are unchanged.'))
        (staging/'README.md').write_text('''# Matched target-visibility comparison

GPT-5-nano/minimal, the same 832 source programs, four n=4 requests per program,
and compose@1/@4/@8/@16 from fixed response prefixes. Generation sees the original
targets in the new visible arm; clause filtering stays target-free in both arms,
and the original targets are restored for final scoring. No adaptive repair is used.

`comparison/` contains final tables, paired cases, costs, and input provenance.
`completion_gate.json` records full verification/audit/replay completion for both
arms and all four prefixes. Semantic unknowns remain unknown and are not certified.
Both arms use the same semantic engine and budgets; the historical exact count in
sibling 06 is not silently substituted for the common-engine hidden control.

`evidence/` preserves generation, original/hidden sources, verifier records/logs,
manifests, invocations/resume histories, audit cases, saved SMT queries, and replay
records. Scratch and Python cache directories are excluded. Bundles use the same
24 MiB part/index format as the other rebuttal archives. Restore with the parent
`restore_evidence.py`; every index includes whole-archive, part, and member hashes.
Historical hidden model replies and prefix proofs are already archived in sibling
01/02. Scripts retain historical paths and need path adaptation after restoration.

@1 usage is an estimate (full first-batch input plus one quarter of its output);
@4/@8/@16 use recorded complete-batch usage. Prefix costs overlap and must not be
summed. Reasoning tokens are included in output tokens. Failed-request billing may
be unavailable. Archiving performs no API calls, training, proof search, or Git writes.
''')
        writer.verify_sources()
        final_gate=check_completion(args)
        require(final_gate['comparison_sha256']==gate['comparison_sha256'],'Comparison changed during archiving')
        dump(staging/'manifest.json',manifest_for(staging))
        staging.rename(destination)
        if args.update_root_index:register_root(destination,root_before)
        print(json.dumps(dict(status='archived',destination=str(destination),root_registered=args.update_root_index,
                              file_count=load(destination/'manifest.json')['file_count'],
                              review_required_before_git=True),indent=2))
    except Exception:
        print('Archive did not finish; preserve and inspect staging/destination:',staging,destination,flush=True)
        raise


if __name__=='__main__':
    main()
