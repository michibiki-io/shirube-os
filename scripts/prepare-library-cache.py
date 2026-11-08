#!/usr/bin/env python3
"""Prepare authenticated native library artifacts before the private ISO build."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--image',required=True)
    parser.add_argument('--architecture',choices=['amd64','arm64'],required=True)
    parser.add_argument('--store',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--shared-output',type=Path)
    parser.add_argument('--verified-input',type=Path)
    parser.add_argument('--cpus',type=int,default=4)
    parser.add_argument('--memory-gib',type=int,default=8)
    args=parser.parse_args()
    project=args.project.resolve()
    with tempfile.TemporaryDirectory(prefix='shirube-library-inputs-') as temporary:
        mounts=['-v',temporary+':/library-inputs']
        source_args=[]
        if args.verified_input:
            mounts+=['-v',str(args.verified_input.resolve())+':/verified:ro']
            source_args=['--verified-input','/verified']
        subprocess.run(['docker','run','--rm','--user',str(os.getuid())+':'+str(os.getgid()),'--cpus='+str(args.cpus),
            '--memory='+str(args.memory_gib)+'g','--memory-swap='+str(args.memory_gib)+'g',
            '--entrypoint','python3',*mounts,args.image,'/src/native/library_inputs.py',
            '--output','/library-inputs',*source_args],check=True)
        shared_args=['--shared-output',str(args.shared_output.resolve())] if args.shared_output else []
        subprocess.run(['python3',str(project/'scripts/build-library-cache.py'),
            '--project',str(project),'--sources',temporary+'/sources',
            '--architecture',args.architecture,'--toolchain',args.image,
            '--store',str(args.store.resolve()),'--output',str(args.output.resolve()),
            '--cpus',str(args.cpus),'--memory-gib',str(args.memory_gib),*shared_args],check=True)


if __name__=='__main__':main()
