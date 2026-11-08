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
from producer.policy import version_parts


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--architecture',choices=['amd64','arm64'],required=True)
    parser.add_argument('--image',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--project-source',type=Path,required=True)
    parser.add_argument('--private',type=Path,required=True)
    parser.add_argument('--version',required=True)
    parser.add_argument('--commit',required=True)
    args=parser.parse_args()
    if not re.fullmatch('sha256:[0-9a-f]{64}',args.image):raise ValueError('Immutable native toolchain image required')
    version_parts(args.version)
    if not re.fullmatch('[0-9a-f]{40}',args.commit):
        raise ValueError('Exact candidate version and commit required')
    output=args.output.resolve();project=args.project_source.resolve()
    if not output.is_dir() or any(output.iterdir()) or not project.is_file():
        raise ValueError('Empty candidate output and exact project source archive required')
    def interrupted(signum, frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted)
    with Fixture(args.architecture,args.image,args.private) as fixture:
        address=fixture.docker('inspect','--format',
            '{{(index .NetworkSettings.Networks "'+fixture.network+'").IPAddress}}',fixture.container).decode().strip()
        # The candidate retains public-input network access. Only its
        # isolated guest-test connection joins the local fixture network.
        build=fixture.docker('create','-i','--network','bridge',
            '--label','io.michibiki.shirube.fixture='+fixture.nonce,
            '--add-host',fixture.hostname+':'+address,
            '--privileged','--device','/dev/kvm','--cpus=2','--memory=8g',
            '--memory-swap=8g','--pids-limit=4096',
            '-v',str(output)+':/output','-v',str(project)+':/project-source.tar:ro',
            '-v',str(fixture.work/'cert.pem')+':/fixture/cert.pem:ro',
            args.image,args.architecture,'--target','release','--version',args.version,
            '--commit',args.commit,'--netbird-fixture-stdin').decode().strip()
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
