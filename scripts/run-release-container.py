#!/usr/bin/env python3
"""Run one native candidate with an ephemeral local NetBird guest fixture."""
import argparse
import json
from pathlib import Path
import re
import signal
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from producer.netbird_fixture import Fixture
from producer.staged import validate_discard_inputs
from producer.policy import version_parts
from producer.architecture import release_accelerator,container_runtime


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--architecture',choices=['amd64','arm64'],required=True)
    parser.add_argument('--image',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--project-source',type=Path,required=True)
    parser.add_argument('--private',type=Path,required=True)
    parser.add_argument('--version',required=True)
    parser.add_argument('--commit',required=True)
    parser.add_argument('--cpus',type=int,default=4)
    parser.add_argument('--memory-gib',type=int,default=8)
    parser.add_argument('--cached-libraries',type=Path)
    parser.add_argument('--staged-inputs',type=Path)
    parser.add_argument('--discard-input-archives',action='store_true')
    parser.add_argument('--qemu-accelerator',choices=['kvm','tcg'],default='kvm')
    parser.add_argument('--docker-runtime',default='')
    args=parser.parse_args()
    if args.discard_input_archives:
        if args.staged_inputs is None:
            raise ValueError('Input retirement requires staged inputs')
        if len(args.staged_inputs.resolve().parts) < 3:
            raise ValueError('Dedicated task input directory required, not a host root')
        validate_discard_inputs('guests', args.staged_inputs, args.output, args.cached_libraries,
                                protected_paths=(args.private, args.project_source))
    release_accelerator(args.architecture,args.qemu_accelerator)
    container_runtime(args.docker_runtime,registered=True)
    if not re.fullmatch('sha256:[0-9a-f]{64}',args.image):raise ValueError('Immutable native toolchain image required')
    version_parts(args.version)
    if not re.fullmatch('[0-9a-f]{40}',args.commit):
        raise ValueError('Exact candidate version and commit required')
    if not 1<=args.cpus<=64 or not 2<=args.memory_gib<=256:raise ValueError('Invalid native resource budget')
    output=args.output.resolve();project=args.project_source.resolve()
    if not output.is_dir() or any(output.iterdir()) or not project.is_file():
        raise ValueError('Empty candidate output and exact project source archive required')
    def interrupted(signum, frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted)
    library_mounts=[];library_args=[]
    if args.cached_libraries:
        library_mounts=['-v',str(args.cached_libraries.resolve())+':/library-cache:ro']
        library_args=['--cached-libraries','/library-cache']
    devices=['--device','/dev/kvm'] if args.qemu_accelerator=='kvm' else []
    with Fixture(args.architecture,args.image,args.private,runtime=args.docker_runtime) as fixture:
        address=fixture.docker('inspect','--format',
            '{{(index .NetworkSettings.Networks "'+fixture.network+'").IPAddress}}',fixture.container).decode().strip()
        # The candidate retains public-input network access. Only its
        # isolated guest-test connection joins the local fixture network.
        producer_command=[args.image,args.architecture,'--target','release','--version',args.version,
                          '--commit',args.commit,'--netbird-fixture-stdin','--qemu-accelerator',args.qemu_accelerator,*library_args]
        if args.staged_inputs:
            library_mounts += ['-v',str(args.staged_inputs.resolve())+':/inputs:'+('rw' if args.discard_input_archives else 'ro')]
            producer_command=['--entrypoint','python3',args.image,'-m','producer.staged','guests',args.architecture,
                              '--commit',args.commit,'--version',args.version,'--netbird-fixture-stdin',
                              '--qemu-accelerator',args.qemu_accelerator,
                              *(['--discard-input-archives'] if args.discard_input_archives else [])]
        build=fixture.docker('create','-i','--network','bridge',
            '--label','io.michibiki.shirube.fixture='+fixture.nonce,
            '--add-host',fixture.hostname+':'+address,
            '--privileged',*devices,'--cpus='+str(args.cpus),'--memory='+str(args.memory_gib)+'g',
            '--memory-swap='+str(args.memory_gib)+'g','--pids-limit=4096',
            '-v',str(output)+':/output','-v',str(project)+':/project-source.tar:ro',
            '-v',str(fixture.work/'cert.pem')+':/fixture/cert.pem:ro',
            *library_mounts,*producer_command).decode().strip()
        fixture.docker('network','connect',fixture.network,build)
        # The producer consumes this before spawning any build subprocess.
        # No token or setup key appears in argv, environment or build logs.
        process=subprocess.Popen(['docker','start','-a','-i',build],stdin=subprocess.PIPE)
        try:
            process.communicate(json.dumps({'url':fixture.url,'address':address,'token':fixture.token}).encode())
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=15)
        if process.returncode or fixture.docker('inspect','--format','{{.State.ExitCode}}',build).decode().strip()!='0':
            raise RuntimeError('Native candidate generation or required validation failed')
        # Fixture.close() verifies ownership and removes the candidate container,
        # management server, helpers and private server state.


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:raise SystemExit(130) from None
    except Exception:
        print('Candidate/fixture run failed; no candidate is cleared for publication',file=sys.stderr)
        raise SystemExit(1) from None
