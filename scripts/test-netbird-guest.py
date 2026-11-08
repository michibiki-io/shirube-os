#!/usr/bin/env python3
"""Host Docker orchestrator for actual installed-guest NetBird acceptance.

The immutable input must be an unprovisioned standalone QCOW2. Only a new
temporary overlay is enrolled; no fixture state is copied into release images.
"""
import argparse
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from producer.netbird_fixture import Fixture
from producer.sources import sha256
from producer.policy import version_parts


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--architecture',choices=['amd64','arm64'],required=True)
    parser.add_argument('--helper-image',required=True)
    parser.add_argument('--disk',type=Path,required=True)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--version',required=True)
    parser.add_argument('--accelerator',choices=['kvm','tcg'],default='kvm')
    args=parser.parse_args()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',args.helper_image):raise ValueError('Immutable toolchain image required')
    version_parts(args.version)
    disk=args.disk.resolve();before=sha256(disk)
    root=args.work.resolve();root.mkdir(parents=True,mode=0o700,exist_ok=False)
    result=root/'result';result.mkdir(mode=0o700)
    # QEMU serial/firmware and peer state are private until removed in finally.
    private=root/'guest-private';private.mkdir(mode=0o700)
    try:
        with Fixture(args.architecture,args.helper_image,root/'management-private') as fixture:
            program='''import json,subprocess,sys
from pathlib import Path
from producer.vm import VM
from producer.netbird_guest import Management
payload=json.load(sys.stdin)
metadata=json.loads(subprocess.check_output(['qemu-img','info','--output=json','/input/base.qcow2']))
if metadata.get('format')!='qcow2' or metadata.get('virtual-size')!=50*1024**3 or metadata.get('backing-filename'):
 raise ValueError('Standalone 50 GiB QCOW2 input required')
clone=Path('/private/instance.qcow2')
subprocess.run(['qemu-img','create','-f','qcow2','-F','qcow2','-b','/input/base.qcow2',str(clone)],check=True,stdout=subprocess.DEVNULL)
vm=VM(payload['architecture'],clone,Path('/private/vm'),accelerator=payload['accelerator'])
try:
 vm.enroll()
 if payload['version'].removeprefix('v') not in vm.command('show version'):raise ValueError('Wrong image version')
 checks=Management(payload).verify(vm)
 vm.poweroff()
finally:vm.close()
Path('/result/evidence.json').write_text(json.dumps({'kind':'guest-netbird-lifecycle','architecture':payload['architecture'],
 'version':payload['version'],'input_qcow2_sha256':payload['disk_sha256'],'accelerator':payload['accelerator'],
 'checks':checks,'complete_image_acceptance':'NOT_RUN','oci':'NOT_RUN'},indent=2)+'\\n')
'''
            address=fixture.docker('inspect','--format','{{(index .NetworkSettings.Networks "'+fixture.network+'").IPAddress}}',fixture.container).decode().strip()
            devices=['--device','/dev/kvm'] if args.accelerator=='kvm' else []
            fixture.helper_run('--network',fixture.network,'--log-driver','none',
                '--add-host',fixture.hostname+':'+address,
                '--cpus=2','--memory=4g','--pids-limit=512',*devices,
                '-v',str(disk)+':/input/base.qcow2:ro',
                '-v',str(fixture.work/'cert.pem')+':/fixture/cert.pem:ro',
                '-v',str(private)+':/private','-v',str(result)+':/result',
                '--entrypoint','python3',args.helper_image,'-c',program,
                input=json.dumps({'architecture':args.architecture,'version':args.version,
                    'accelerator':args.accelerator,'disk_sha256':before,
                    'url':fixture.url,'address':address,'token':fixture.token}).encode(),timeout=7200)
    finally:
        # Remove root-owned, disposable guest data with the same owned helper;
        # never delete the read-only input or another task's directory.
        import subprocess
        subprocess.run(['docker','run','--rm','--network','none','--log-driver','none',
            '--label','io.michibiki.shirube.task=netbird-guest-cleanup',
            '-v',str(private)+':/private','--entrypoint','python3',args.helper_image,'-c',
            'from pathlib import Path; import shutil; p=Path("/private"); [shutil.rmtree(x) if x.is_dir() and not x.is_symlink() else x.unlink() for x in p.iterdir()]'],check=True)
        private.rmdir()
    if sha256(disk)!=before:raise ValueError('Immutable input changed during guest verification')
    if not (result/'evidence.json').is_file():raise ValueError('Guest verification produced no evidence')
    print('PASS: actual guest NetBird configuration/enrollment/disable/reboot/removal; complete image/OCI acceptance remains separate')


if __name__=='__main__':main()
