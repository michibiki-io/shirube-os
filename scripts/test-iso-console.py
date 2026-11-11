#!/usr/bin/env python3
"""Container-only bounded ISO live-console probe; not installed-image acceptance."""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,'/src')
from producer.sources import sha256
from producer.vm import VM
import subprocess


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--architecture',choices=['amd64','arm64'],required=True)
    parser.add_argument('--accelerator',choices=['kvm','tcg'],required=True)
    parser.add_argument('--iso',type=Path,required=True)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--version',required=True)
    args=parser.parse_args()
    args.work.mkdir(mode=0o700,parents=True,exist_ok=False)
    before=sha256(args.iso);disk=args.work/'temporary.qcow2'
    subprocess.run(['qemu-img','create','-f','qcow2',str(disk),'50G'],check=True,stdout=subprocess.DEVNULL)
    start=time.monotonic();vm=VM(args.architecture,disk,args.work/'live',args.iso,args.accelerator)
    try:
        vm.enroll()
        if vm.command('sudo -n id -u')!='0':raise ValueError('Instance administrator access failed')
        arch=vm.command('uname -m')
        if arch not in ({'aarch64','arm64'} if args.architecture=='arm64' else {'amd64','x86_64'}):
            raise ValueError('Wrong running guest architecture')
        version=vm.command('show version')
        if args.version.removeprefix('v') not in version or 'Shirube OS' not in version:
            raise ValueError('Wrong guest product identity/version')
        route=json.loads(vm.command('ip -j -4 route'))
        if not any(x.get('dst')=='default' and x.get('gateway')=='10.0.2.2' for x in route):
            raise ValueError('Live guest default route missing')
        vm.poweroff()
    finally:vm.close()
    if sha256(args.iso)!=before:raise ValueError('Read-only ISO changed during probe')
    (args.work/'evidence.json').write_text(json.dumps({'kind':'iso-live-console-probe',
        'architecture':args.architecture,'accelerator':args.accelerator,'iso_sha256':before,
        'version':args.version,'live_console_enrollment_and_access':'PASS','live_default_route':'PASS',
        'elapsed_seconds':round(time.monotonic()-start,2),'boot_timeout_seconds':1200 if args.accelerator=='tcg' else 300,
        'installed_disks':'NOT_RUN','final_sanitized_boot':'NOT_RUN','netbird_guest_configuration':'NOT_RUN',
        'oci':'NOT_RUN','release_clearance':'NOT_CLEARED'},indent=2)+'\n')
    print('PASS: ISO live-console probe; installed-image and full-release acceptance remain NOT_RUN')


if __name__=='__main__':main()
