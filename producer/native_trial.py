"""Measure the native ISO/source phase without publishing build artifacts."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from .architecture import native
from .policy import require_stream_version
from .release import filename
from .sources import sha256


def receipt(output, architecture, version, commit):
    """An earlier or partial ISO must never turn a failed trial into success."""
    output=Path(output)
    record=json.loads((output/'iso-build.json').read_text())
    expected={'kind':'iso-build','project':'Shirube OS','architecture':architecture,
              'version':version,'source_commit':commit,'source_signature':'PASS',
              'source_collection':'PASS','iso':f'shirube-os-{version}-{architecture}.iso'}
    if any(record.get(key)!=value for key,value in expected.items()):
        raise ValueError('Native trial receipt is stale or incomplete')
    image=output/record['iso']
    if image.is_symlink() or not image.is_file() or sha256(image)!=record.get('sha256'):
        raise ValueError('Native trial ISO bytes differ')
    names=record.get('source_archives',[])
    inventory=record.get('source_inventory')
    if (not names or len(names)!=len(set(names)) or
            inventory!=f'shirube-os-{version}-{architecture}-licenses.json'):
        raise ValueError('Native trial source declaration is incomplete')
    for name in names+[inventory]:
        path=output/filename(name)
        if (not name.startswith(f'shirube-os-{version}-{architecture}-') or
                path.is_symlink() or not path.is_file()):
            raise ValueError('Native trial source input is missing or unsafe')
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('architecture',choices=['amd64','arm64'])
    parser.add_argument('--storage',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args();native(args.architecture)
    commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    if subprocess.check_output(['git','status','--porcelain'],text=True).strip():
        raise ValueError('Native trial requires an exact clean checkout')
    lock=json.loads(Path('source-lock.json').read_text())
    version='v'+lock['stream']['version']+'.0'
    require_stream_version(version,lock['stream']['version'])
    if args.report.exists() or args.report.is_symlink():
        raise ValueError('Fresh native trial report required')
    work=Path(tempfile.mkdtemp(prefix='shirube-native-trial-',dir=args.storage))
    output=work/'iso'
    environment={**os.environ,'SHIRUBE_ARCHITECTURE':args.architecture,
        'SHIRUBE_VERSION':version,'SHIRUBE_SOURCE_COMMIT':commit,
        'SHIRUBE_ISO_DIRECTORY':str(output),'SHIRUBE_INCLUDE_SOURCES':'1',
        'SHIRUBE_BUILD_STATE_DIRECTORY':str(work/'state'),
        'SHIRUBE_LIBRARY_CACHE_STORE':str(work/'cold-library-store'),
        'SHIRUBE_PRESERVE_FAILED_BUILD':'0','TMPDIR':str(work)}
    baseline=shutil.disk_usage(args.storage).free
    measurement={'minimum':baseline,'samples':0}
    stopped=threading.Event()
    def sample():
        while not stopped.is_set():
            measurement['minimum']=min(measurement['minimum'],shutil.disk_usage(args.storage).free)
            measurement['samples']+=1
            stopped.wait(15)
    sampler=threading.Thread(target=sample,daemon=True)
    started=time.monotonic()
    report={'kind':'native-hosted-iso-source-resource-trial','status':'FAIL',
        'source_commit':commit,'architecture':args.architecture,'version':version,
        'library_cache':'COLD_TASK_STORE','release_clearance':'NOT_CLEARED','oci':'NOT_RUN',
        'scope':'Native library preparation, ISO and complete source-set restoration only; excludes compiler-image preparation and all disk/guest/release acceptance.',
        'storage_sampling':'15-second samples are a lower bound on peak usage, not an exact peak or a complete release capacity guarantee.'}
    process=None
    sampler.start()
    try:
        process=subprocess.Popen(['bash','scripts/build-iso.sh'],env=environment)
        code=process.wait()
        report['producer_exit_code']=code
        if code:raise ValueError('Native ISO/source producer failed')
        record=receipt(output,args.architecture,version,commit)
        project=work/'project-source.tar'
        with project.open('xb') as stream:
            subprocess.run(['git','archive','--format=tar',commit],stdout=stream,check=True)
        from .pipeline import source_set_evidence
        sources={'inventory':output/record['source_inventory'],
                 'archives':[output/name for name in record['source_archives']],
                 'cleared':record.get('license_clearance')=='PASS'}
        restored=source_set_evidence(sources,output,args.architecture,version,commit,
                                     work/'restored',project=project)
        report.update(status='PASS_ISO_SOURCE_PHASE_ONLY',iso_sha256=record['sha256'],
                      source_restoration=restored['proof'],
                      component_license_clearance=record.get('license_clearance','NOT_RUN'))
    except Exception as error:
        report['error_type']=type(error).__name__
    finally:
        stopped.set();sampler.join()
        if process is not None and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=30)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        minimum=min(measurement['minimum'],shutil.disk_usage(args.storage).free)
        report.update(elapsed_seconds=round(time.monotonic()-started,3),
            baseline_free_bytes=baseline,minimum_sampled_free_bytes=minimum,
            sampled_storage_growth_bytes=baseline-minimum,storage_samples=measurement['samples'])
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2)+'\n')
    # Only this sanitized report may be uploaded. ISO, source and library-cache
    # directories stay in the disposable job until their separate release gate.
    print(json.dumps(report,indent=2),flush=True)
    if report['status']!='PASS_ISO_SOURCE_PHASE_ONLY':raise SystemExit(1)


if __name__=='__main__':main()
