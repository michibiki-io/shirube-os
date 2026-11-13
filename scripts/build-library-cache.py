#!/usr/bin/env python3
"""Build/reuse artifact-only library images, never release/signing containers."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from producer.library_cache import context,eligible,identity,verify,verify_image_archive
from producer.sources import sha256


def run(*args,**kwargs):return subprocess.run(args,check=True,**kwargs)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--sources',type=Path,required=True,help='Authenticated Stream source root')
    parser.add_argument('--architecture',choices=['amd64','arm64'],required=True)
    parser.add_argument('--toolchain',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--store',type=Path,required=True,help='Artifact-only image archives; no compiler workspace')
    parser.add_argument('--shared-output',type=Path,help='New curated directory containing only verified selected archives')
    parser.add_argument('--job',action='append')
    parser.add_argument('--cpus',type=int,default=4)
    parser.add_argument('--memory-gib',type=int,default=8)
    args=parser.parse_args();project=args.project.resolve();output=args.output.resolve()
    if not re.fullmatch('sha256:[0-9a-f]{64}',args.toolchain):raise ValueError('Immutable compiler image required')
    actual=subprocess.check_output(['docker','image','inspect',args.toolchain,'--format','{{.Architecture}}'],text=True).strip()
    if actual!=args.architecture:raise ValueError('Compiler image architecture differs')
    if not 1<=args.cpus<=64 or not 2<=args.memory_gib<=256:raise ValueError('Invalid compiler resource budget')
    fingerprint=subprocess.check_output(['docker','run','--rm','--network','none','--workdir','/cache-code','--entrypoint','python3',
        '-e','PYTHONPATH=/cache-code','-v',str(project/'producer')+':/cache-code/producer:ro',args.toolchain,
        '-c','from producer.library_cache import compiler_fingerprint; print(compiler_fingerprint())'],text=True).strip()
    compiler_tag='shirube-toolchain-local:'+args.toolchain.removeprefix('sha256:')
    run('docker','tag',args.toolchain,compiler_tag)
    native=project/'containers/native';catalog=json.loads((native/'packages.json').read_text())
    jobs=[job for job in catalog['jobs'] if eligible(job)]
    names={job['name'] for job in jobs}
    if any(set(job.get('requires',[]))-names for job in jobs):raise ValueError('Library graph includes an uncached signing/core dependency')
    if args.job:
        selected=set()
        def select(name):
            if name not in names:raise ValueError('Only unsigned library jobs can be cached')
            if name in selected:return
            selected.add(name)
            for dependency in next(job for job in jobs if job['name']==name).get('requires',[]):select(dependency)
        for name in args.job:select(name)
        jobs=[job for job in jobs if job['name'] in selected]
    if output.exists():raise ValueError('New cache-export directory required')
    output.mkdir(parents=True);args.store.mkdir(parents=True,exist_ok=True)
    completed={};index={}
    with tempfile.TemporaryDirectory(prefix='shirube-library-context-') as temporary:
        for job in jobs:
            dependencies=[path for name in job.get('requires',[]) for path in completed[name].glob('*.deb')]
            source=args.sources/job['source']
            if job.get('recipe') == 'frr':
                sys.path.insert(0, str(native))
                from security_sources import prepare_frr
                prepare_frr(project, source)
            expected,_=identity(project,job,source,dependencies,args.architecture,fingerprint)
            key=expected['input_id'];image='shirube-library-cache:'+args.architecture+'-'+key
            saved=args.store/(args.architecture+'-'+key+'.tar')
            if saved.is_symlink():raise ValueError('Library image archive must not be a link')
            present=subprocess.run(['docker','image','inspect',image],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
            if not present and saved.is_file():
                verify_image_archive(saved,image,args.architecture)
                run('docker','load','--input',str(saved));present=True
            if not present:
                build=Path(temporary)/job['name']
                context(project,job,source,dependencies,args.architecture,fingerprint,build)
                print('LIBRARY BUILD: '+job['name']+' '+key,flush=True)
                run('docker','buildx','build','--builder','default','--load','--platform','linux/'+args.architecture,
                    '--resource','memory='+str(args.memory_gib)+'g','--resource','memory-swap='+str(args.memory_gib)+'g',
                    '--resource','cpu-quota='+str(args.cpus*100000),'--build-arg','TOOLCHAIN_IMAGE='+compiler_tag,
                    '--tag',image,str(build))
            else:print('LIBRARY REUSE: '+job['name']+' '+key,flush=True)
            destination=output/job['name'];destination.mkdir()
            nonce='library-export-'+key
            cid=subprocess.check_output(['docker','create','--label','io.michibiki.shirube.task='+nonce,
                                         '--entrypoint','/not-executed',image],text=True).strip()
            try:run('docker','cp',cid+':/library/.',str(destination))
            finally:
                label=subprocess.check_output(['docker','inspect','--format','{{index .Config.Labels "io.michibiki.shirube.task"}}',cid],text=True).strip()
                if label!=nonce:raise ValueError('Library export container ownership differs')
                run('docker','rm',cid,stdout=subprocess.DEVNULL)
            receipt=verify(destination,expected)
            # Archives contain only the scratch artifact image, never the
            # compiler image/layers. Shared upload remains gated by release CI.
            if not saved.exists():
                pending=saved.with_suffix('.partial')
                run('docker','save','--output',str(pending),image);pending.replace(saved)
            verify_image_archive(saved,image,args.architecture)
            completed[job['name']]=destination
            index[job['name']]={'input_id':key,'toolchain':args.toolchain,'compiler_fingerprint':fingerprint,
                               'packages':receipt['packages'],'archive_sha256':sha256(saved)}
    (output/'index.json').write_text(json.dumps({'schema_version':1,'architecture':args.architecture,'jobs':index},indent=2)+'\n')
    if args.shared_output:
        args.shared_output.mkdir(parents=True,exist_ok=False)
        for entry in index.values():
            name=args.architecture+'-'+entry['input_id']+'.tar'
            archive=args.store/name
            verify_image_archive(archive,'shirube-library-cache:'+args.architecture+'-'+entry['input_id'],args.architecture)
            if sha256(archive)!=entry['archive_sha256']:raise ValueError('Library archive changed before shared export')
            shutil.copy2(archive,args.shared_output/name)


if __name__=='__main__':main()
