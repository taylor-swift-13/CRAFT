"""Locally complete NLA_lipus_7 prefixes omitted from the frozen 831-case wave.

Reuses the frozen canonical prefix driver in a new auxiliary run, one proof job
at a time. Never calls a model or changes canonical protocol/export/ready ledger.
Complete True and False outcomes are equally eligible for no-overwrite publication.
Run --check-only before execution. This helper is intentionally restricted to
the one late case and refuses to overlap the canonical frozen ready set.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

sys.dont_write_bytecode=True
KEY='NLA_lipus_7'
PREFIXES=[1,4,8]
BASE=Path('/home/yangfp/craft_empirical_runs/craft16_target_visible_20261110')
CANONICAL=Path('/home/yangfp/craft_empirical_runs/craft16_target_visible_prefixes_20261110')
AUX=Path('/home/yangfp/craft_empirical_runs/craft16_target_visible_late_prefix_NLA_lipus_7_20261110')
THIS=Path(__file__).resolve()


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def encode(value):
    return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()


def require(condition,message):
    if not condition:raise RuntimeError(message)


def exclusive_publish(path,data):
    """Stage with O_EXCL, fsync, then hard-link the complete bytes without replacement.

    Direct O_EXCL on the final JSON would expose a partially written record. The
    same-directory hard link publishes complete data atomically and fails EEXIST.
    Only the helper's unique temporary file is removed; existing targets are untouched.
    """
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+f'.late-tmp-{os.getpid()}-{time.time_ns()}')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        try:
            os.link(temp,path)
        except FileExistsError:
            return False
        dfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
        return True
    finally:
        if temp.exists():temp.unlink()


def dump_new(path,value):
    require(exclusive_publish(path,encode(value)),'Refusing to overwrite existing helper output: '+str(path))


def no_canonical_claim(k=None):
    ledger=read(CANONICAL/'frozen_completed_inputs.json')
    require(KEY not in ledger.get('cases',{}),'Canonical ready set now includes '+KEY+'; do not duplicate its jobs')
    ks=PREFIXES if k is None else [k]
    return all(not (CANONICAL/'verification'/f'{KEY}_k{value}.json').exists() for value in ks)


def frozen_inputs_unchanged(frozen):
    for path,checksum in frozen.items():
        require(Path(path).is_file() and digest(path)==checksum,'Frozen input changed: '+path)


def preflight():
    protocol_path=CANONICAL/'protocol.json'
    driver_path=CANONICAL/'driver.py'
    manifest_path=BASE/'manifest.json'
    generation_path=BASE/'generations'/(KEY+'.json')
    baseline_path=BASE/'verification'/(KEY+'.json')
    source_path=BASE/'sources'/(KEY+'.original.c')
    hidden_path=BASE/'sources'/(KEY+'.hidden.c')
    require(no_canonical_claim(),'Canonical prefix result already exists; helper must not start')
    for k in PREFIXES:
        require(not (CANONICAL/'logs'/f'{KEY}_k{k}.jsonl.gz').exists(),
                'Canonical prefix log already exists; inspect ownership before starting')
    protocol=read(protocol_path);manifest=read(manifest_path)
    require(protocol.get('driver_sha256')==digest(driver_path),'Frozen prefix driver hash mismatch')
    require(protocol.get('source_fingerprint')==manifest.get('fingerprint'),'Source fingerprint mismatch')
    require(protocol.get('source_manifest_sha256')==digest(manifest_path),'Source manifest hash mismatch')
    require(protocol.get('target_hidden') is False and protocol.get('target_free_filtering') is True,
            'Unexpected visibility/filtering protocol')
    require(KEY in protocol.get('expected_keys',[]),'Late case is absent from expected benchmark keys')
    spec=importlib.util.spec_from_file_location('late_case_frozen_prefix_driver',driver_path)
    driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
    driver.validate_manifest(manifest)
    expected={Path(path).parent.name+'_'+Path(path).stem:checksum
              for path,checksum in manifest['original_inputs'].items()}
    require(digest(source_path)==expected[KEY],'Original source hash mismatch')
    for path,checksum in {**manifest['sources'],**manifest['original_inputs']}.items():
        require(Path(path).is_file() and digest(path)==checksum,'Source/code provenance drift: '+path)
    task=next((task for task in driver.discover_tasks() if task.suite+'_'+task.case_id==KEY),None)
    require(task is not None,'Cannot locate the late program')
    task=replace(task,source_path=source_path)
    require(task.hidden_source==hidden_path.read_text(),'Hidden filtering source drift')
    generation=read(generation_path);baseline=read(baseline_path)
    require(generation.get('status')=='complete' and generation.get('fingerprint')==manifest['fingerprint'],
            'Generation is incomplete or from a different protocol')
    require(baseline.get('status')=='complete' and isinstance(baseline.get('verified'),bool)
            and baseline.get('fingerprint')==manifest['fingerprint'],'@16 result is incomplete or inconsistent')
    require(baseline.get('generation_sha256')==digest(generation_path),'@16 generation hash mismatch')
    calls=generation.get('calls',[])
    require(len(calls)==4 and all(call.get('requested_n')==4 and len(call.get('raw_responses',[]))==4 for call in calls),
            'Unexpected generation batch schedule')
    require(generation.get('responses')==[answer for call in calls for answer in call['raw_responses']],
            'Response ordering mismatch')
    original=source_path.read_text(errors='ignore')
    require(any(message.get('role')=='user' and original in message.get('content','')
                for message in generation.get('messages',[])),'Visible generation lacks the original source')
    paths=[protocol_path,driver_path,manifest_path,generation_path,baseline_path,source_path,hidden_path,THIS]
    frozen={str(path):digest(path) for path in paths}
    job_hashes=(digest(generation_path),digest(baseline_path))
    return driver,task,frozen,job_hashes


def publish(k,result,frozen):
    name=f'{KEY}_k{k}'
    source_result=AUX/'verification'/(name+'.json')
    source_log=AUX/'logs'/(name+'.jsonl.gz')
    canonical_result=CANONICAL/'verification'/(name+'.json')
    receipt_dir=CANONICAL/'late_prefix_receipts'/name
    receipt=dict(key=KEY,k=k,utc=utc(),aux_run=str(AUX),script_sha256=frozen[str(THIS)],
                 frozen_driver_sha256=frozen[str(CANONICAL/'driver.py')],
                 frozen_inputs=frozen,
                 source_result=str(source_result),canonical_result=str(canonical_result),
                 status=result.get('status'),verified=result.get('verified'),api_calls=0)
    if result.get('status')!='complete' or not isinstance(result.get('verified'),bool):
        receipt.update(published=False,reason='auxiliary_verification_not_complete')
        return receipt
    require(result.get('key')==KEY and result.get('k')==k,'Wrong auxiliary result identity')
    require(result.get('generation_sha256')==frozen[str(BASE/'generations'/(KEY+'.json'))]
            and result.get('base_verification_sha256')==frozen[str(BASE/'verification'/(KEY+'.json'))],
            'Auxiliary result provenance mismatch')
    frozen_inputs_unchanged(frozen)
    if not no_canonical_claim(k):
        receipt.update(published=False,reason='canonical_result_already_exists');return receipt
    require(source_log.is_file(),'Completed result has no evidence log')
    # Ensure the evidence file is a complete gzip stream before publication.
    with gzip.open(source_log,'rt') as stream:
        for line in stream:
            json.loads(line)
    result_bytes=source_result.read_bytes();log_bytes=source_log.read_bytes()
    receipt.update(result_sha256=hashlib.sha256(result_bytes).hexdigest(),
                   log_sha256=hashlib.sha256(log_bytes).hexdigest(),source_log=str(source_log),
                   canonical_log=str(CANONICAL/'logs'/(name+'.jsonl.gz')))
    if not exclusive_publish(CANONICAL/'logs'/(name+'.jsonl.gz'),log_bytes):
        receipt.update(published=False,reason='canonical_log_already_exists');return receipt
    # Metadata and producer snapshots precede the complete result record.
    dump_new(receipt_dir/'evidence.json',receipt)
    require(exclusive_publish(receipt_dir/'helper.py',THIS.read_bytes()),'Helper snapshot already exists')
    require(exclusive_publish(receipt_dir/'prefix_driver.py',(CANONICAL/'driver.py').read_bytes()),'Driver snapshot already exists')
    require(exclusive_publish(receipt_dir/'result.json',result_bytes),'Receipt result snapshot already exists')
    frozen_inputs_unchanged(frozen)
    if not no_canonical_claim(k):
        receipt.update(published=False,reason='canonical_claim_changed_before_result_publication')
    else:
        receipt.update(published=exclusive_publish(canonical_result,result_bytes),
                       reason='published_complete_result_without_overwrite')
        if not receipt['published']:receipt['reason']='canonical_result_won_publication_race'
    dump_new(receipt_dir/'publication.json',receipt)
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    driver,task,frozen,job_hashes=preflight()
    if args.check_only:
        print(json.dumps(dict(key=KEY,prefixes=PREFIXES,ready=True,aux_run=str(AUX),
                              api_calls=0,proof_settings='same frozen initialize/verify functions'),indent=2));return
    require(not AUX.exists(),'Auxiliary directory already exists; inspect instead of re-running: '+str(AUX))
    AUX.mkdir()
    for name in ['verification','logs','scratch','receipts']:(AUX/name).mkdir()
    dump_new(AUX/'protocol.json',dict(key=KEY,prefixes=PREFIXES,utc=utc(),api_calls=0,workers=1,
        canonical_prefix_run=str(CANONICAL),frozen_inputs=frozen,
        canonical_ledger_sha256_at_start=digest(CANONICAL/'frozen_completed_inputs.json'),
        publication='O_EXCL staging followed by atomic no-replace hard link; complete True/False treated identically'))
    require(exclusive_publish(AUX/'helper.py',THIS.read_bytes()),'Auxiliary helper snapshot exists')
    require(exclusive_publish(AUX/'prefix_driver.py',(CANONICAL/'driver.py').read_bytes()),'Auxiliary driver snapshot exists')
    driver.BASE=BASE;driver.RUN=AUX
    driver.initialize()
    receipts=[]
    for k in PREFIXES:
        frozen_inputs_unchanged(frozen)
        if not no_canonical_claim(k):
            receipt=dict(key=KEY,k=k,published=False,reason='canonical_result_already_exists_before_proof',utc=utc())
        else:
            result=driver.verify((KEY,k,task,*job_hashes))
            receipt=publish(k,result,frozen)
        dump_new(AUX/'receipts'/f'k{k}.json',receipt)
        receipts.append(receipt);print(json.dumps(receipt),flush=True)
    frozen_inputs_unchanged(frozen)
    dump_new(AUX/'summary.json',dict(utc=utc(),key=KEY,api_calls=0,receipts=receipts,
        canonical_protocol_unchanged=digest(CANONICAL/'protocol.json')==frozen[str(CANONICAL/'protocol.json')],
        note='No canonical protocol, global ready ledger, or aggregate export was written. All auxiliary results and logs are retained.'))
    if any(not receipt.get('published') for receipt in receipts):
        raise SystemExit('Some results were not published; inspect receipts. No existing canonical result was overwritten.')


if __name__=='__main__':
    main()
