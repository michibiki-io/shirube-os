"""Compile stage execution and untrusted acceleration, physically downstream-free."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tarfile
import time
from .native_stages import COMPILE_STAGES, DEPENDENCIES
from .compile_scope import build_scope
from .stage_artifacts import attribution, pack, unpack
from .sources import sha256
from .stage_transport import merge_tree, restore_inputs as restore_transfers


def restore_inputs(*args):
    return restore_transfers(*args, dependencies=DEPENDENCIES, build_scope=build_scope)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=COMPILE_STAGES)
    parser.add_argument('architecture', choices=('amd64', 'arm64'))
    parser.add_argument('--commit', required=True); parser.add_argument('--version', required=True)
    parser.add_argument('--inputs', type=Path, default=Path('/inputs'))
    parser.add_argument('--output', type=Path, default=Path('/output'))
    parser.add_argument('--qemu-accelerator', choices=('kvm','tcg'), default='tcg')
    parser.add_argument('--netbird-fixture-stdin', action='store_true')
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--discard-input-archives', action='store_true',
                        help='ISO, clearance and guests only: retire task-owned input tars after verification and metrics')
    args = parser.parse_args()
    if args.discard_input_archives:
        raise ValueError('--discard-input-archives requires ISO, clearance or guests')
    from .architecture import native
    native(args.architecture); attribution(args.commit, args.version)
    from .policy import require_stream_version
    require_stream_version(args.version, json.loads(Path('/src/source-lock.json').read_text())['stream']['version'])
    if args.netbird_fixture_stdin: raise ValueError('Compiler stages cannot consume fixture credentials')
    if any(args.output.iterdir()): raise ValueError('Fresh stage output required')
    work = Path('/build-private'); work.mkdir(mode=0o700)
    from .library_cache import compiler_fingerprint
    compiler = compiler_fingerprint()
    from .stage_metrics import measure
    details={**attribution(args.commit,args.version),'cache':'DISABLED','transfer':'NOT_RECORDED'}
    with measure(args.stage, args.architecture, args.output / 'resources.json', work,details=details):
        transfer_started=time.monotonic()
        dependencies, payloads = restore_inputs(args.inputs, Path('/stage-dependencies'), args.stage,
                                                args.architecture, args.commit, args.version, compiler)
        details['incoming_verify_unpack_seconds']=round(time.monotonic()-transfer_started,3)
        details['inputs']=[{'stage':stage,**{k:json.loads((args.inputs/(stage+'.json')).read_text())[k]
                                          for k in ('sha256','bytes')}} for stage in payloads]
        expected = build_scope(Path('/src'), args.stage, args.architecture, compiler, dependencies)
        details['input_id']=expected['input_id'];details['compiler_fingerprint']=compiler
        cache_status = 'DISABLED'; cached = False
        if args.cache and args.stage in COMPILE_STAGES and args.stage != 'kernel':
            entry = args.cache / expected['input_id']
            cache_status = 'MISS'
            if (entry / 'transfer.json').is_file():
                try:
                    meta = json.loads((entry / 'transfer.json').read_text())
                    if not isinstance(meta,dict):raise ValueError('Invalid cached transfer metadata')
                    if meta.get('scope') == expected:
                        receipt=unpack(entry / 'transfer.tar', work / 'cached', expected, meta['source_commit'], meta['version'], meta['sha256'])
                        if meta!={**receipt,'sha256':sha256(entry/'transfer.tar'),'bytes':(entry/'transfer.tar').stat().st_size}:
                            raise ValueError('Cached transfer metadata differs')
                        merge_tree(work / 'cached', work / 'payload')
                        cached = True; cache_status = 'HIT'
                except (ValueError, OSError, KeyError, tarfile.TarError):
                    # Caches are untrusted acceleration. No invalid cached byte
                    # is consumed; a clean source build must still pass gates.
                    cache_status = 'MISS_INVALID'
                    for stale in (work/'cached',work/'payload'):
                        if stale.exists():shutil.rmtree(stale)
        details['cache']=cache_status
        payload = work / 'payload'
        if not cached:
            payload.mkdir()
            if args.stage in COMPILE_STAGES:
                # Existing bounded package recipes run in this stage's private
                # container; native/light dependencies stay together, heavy
                # independent work runs in separate hosted jobs.
                sys.path.insert(0, '/src/native')
                from native import build_compile as build
                # build.main uses this same task-private workspace.
                sys.argv = ['build.py', args.architecture, '--stage', args.stage, '--output', str(args.output)]
                payload.rmdir()
                build.main()
                payload = args.output / 'stage'
                # Retire internal package outputs from the transferable output.
        transfer_started=time.monotonic()
        transfer = pack(payload, args.output / 'transfer.tar', expected, args.commit, args.version,
                        content_source_commit=meta.get('content_source_commit',meta['source_commit']) if cached else None)
        (args.output / 'transfer.json').write_text(json.dumps(transfer, sort_keys=True)+'\n')
        details['transfer']={'sha256':transfer['sha256'],'bytes':transfer['bytes'],
                             'pack_seconds':round(time.monotonic()-transfer_started,3)}
        if args.cache and args.stage in COMPILE_STAGES and args.stage != 'kernel' and not cached:
            entry = args.cache / expected['input_id']
            if entry.exists():shutil.rmtree(entry)
            entry.mkdir(parents=True, exist_ok=False)
            shutil.copyfile(args.output / 'transfer.tar', entry / 'transfer.tar')
            shutil.copyfile(args.output / 'transfer.json', entry / 'transfer.json')
        # Remove redundant task-owned exports after the verified archive exists.
        if payload.is_relative_to(args.output):
            shutil.rmtree(payload)
            if args.stage in COMPILE_STAGES:
                from .native_stages import select_jobs
                catalog=json.loads(Path('/src/native/packages.json').read_text())['jobs']
                for job in select_jobs(catalog,args.stage):
                    if (args.output/job['name']).is_dir():shutil.rmtree(args.output/job['name'])
                (args.output/'source-preparation.json').unlink(missing_ok=True)
