#!/usr/bin/env python3
"""Hosted stage launcher; no publishing and no credentials mounted in builds."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from producer.staged import STAGES
from producer.architecture import native
from producer.stage_artifacts import attribution
from producer.sources import sha256


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=STAGES, required=True)
    parser.add_argument('--architecture', choices=('amd64','arm64'), required=True)
    parser.add_argument('--commit', required=True); parser.add_argument('--version', required=True)
    parser.add_argument('--image', required=True); parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True); parser.add_argument('--cache', type=Path)
    args = parser.parse_args()
    native(args.architecture); attribution(args.commit, args.version)
    if (not re.fullmatch('sha256:[0-9a-f]{64}', args.image)
            or subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()!=args.commit
            or subprocess.check_output(['git','status','--porcelain'],text=True).strip()):
        raise ValueError('Immutable image and exact clean resolved checkout required')
    actual = subprocess.check_output(['docker','image','inspect',args.image,'--format','{{.Architecture}}'],text=True).strip()
    if actual != args.architecture: raise ValueError('Native compiler image required')
    args.output.mkdir(parents=True, exist_ok=False)
    args.inputs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='shirube-stage-') as temporary:
        project = Path(temporary) / 'project-source.tar'
        with project.open('wb') as output: run('git','archive','--format=tar',args.commit,stdout=output)
        if args.stage == 'guests':
            run(sys.executable,'scripts/run-release-container.py','--architecture',args.architecture,
                '--image',args.image,'--output',str(args.output.resolve()),'--project-source',str(project),
                '--private',str(Path(temporary)/'fixture'),'--version',args.version,'--commit',args.commit,
                '--qemu-accelerator','tcg','--staged-inputs',str(args.inputs.resolve()))
        else:
            cache_args=[]; cache_mount=[]
            if args.cache:
                args.cache.mkdir(parents=True,exist_ok=True)
                cache_mount=['-v',str(args.cache.resolve())+':/stage-cache']
                cache_args=['--cache','/stage-cache']
            # ISO assembly alone requires mount privileges. No Docker socket,
            # host identities, host PID/network or publication token is shared.
            privileged=['--privileged'] if args.stage=='iso' else []
            run('docker','run','--rm',*privileged,'--cpus=4','--memory=8g','--memory-swap=8g','--pids-limit=4096',
                '-v',str(args.output.resolve())+':/output', '-v',str(args.inputs.resolve())+':/inputs:ro',
                '-v',str(project)+':/project-source.tar:ro',*cache_mount,
                '--entrypoint','python3',args.image,'-m','producer.staged',args.stage,args.architecture,
                '--commit',args.commit,'--version',args.version,*cache_args)
    if args.stage != 'guests':
        for suffix in ('tar','json'):
            (args.output/('transfer.'+suffix)).rename(args.output/(args.stage+'.'+suffix))
    (args.output/'resources.json').rename(args.output/(args.stage+'-resources.json'))
    # Release assets remain in candidate/ for the separate credentialed staging
    # job. The launcher itself cannot call GitHub or mutate a draft release.


if __name__ == '__main__': main()
