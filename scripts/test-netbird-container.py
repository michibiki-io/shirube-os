#!/usr/bin/env python3
"""Real client/server smoke test; does not certify the guest OS configuration CLI."""
import argparse
import json
from pathlib import Path
import re
import sys
import tarfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from producer.architecture import ARCHITECTURES, check_elf
from producer.netbird_fixture import Fixture, LABEL
from producer.sources import download, sha256


def connected(fixture, client):
    deadline=time.monotonic()+120
    while True:
        status=json.loads(fixture.docker('exec',client,'/netbird','status','--json'))
        if status.get('management',{}).get('connected') and status.get('signal',{}).get('connected'):
            if not status.get('publicKey'):raise ValueError('Connected client has no public identity')
            return status
        if time.monotonic()>=deadline:raise RuntimeError('Actual NetBird reconnection timed out')
        time.sleep(2)


def verify(args):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',args.helper_image):
        raise ValueError('An immutable native toolchain image ID is required')
    root=args.work.resolve();root.mkdir(parents=True,mode=0o700,exist_ok=False)
    lock=json.loads((Path(__file__).resolve().parents[1]/'source-lock.json').read_text())['netbird']
    if lock['version']!='0.82.0':raise ValueError('Update and validate the management fixture with the client baseline')
    with Fixture(args.architecture,args.helper_image,root/'private') as fixture:
        actual=fixture.docker('info','--format','{{.Architecture}}').decode().strip()
        if actual not in ARCHITECTURES[args.architecture]['aliases']:
            raise ValueError('A native Docker daemon is required for the selected test architecture')
        image_arch=fixture.docker('image','inspect','--format','{{.Architecture}}',args.helper_image).decode().strip()
        if image_arch!=args.architecture:raise ValueError('Wrong native helper image architecture')
        archive=root/'client.tar.gz';binary=root/'netbird'
        asset=lock['assets'][args.architecture];download(asset['url'],archive)
        if sha256(archive)!=asset['sha256']:raise ValueError('Client archive checksum mismatch')
        with tarfile.open(archive) as source:
            member=source.getmember('netbird')
            if not member.isfile() or member.size>256*1024**2:raise ValueError('Invalid headless client payload')
            data=source.extractfile(member).read();check_elf(data,args.architecture);binary.write_bytes(data);binary.chmod(0o755)
        client=None
        try:
            client=fixture.docker('run','-d','--network',fixture.network,'--label',LABEL+'='+fixture.nonce,
                '--log-driver','none','--cap-add','NET_ADMIN','--device','/dev/net/tun',
                '--cpus=1','--memory=512m','--pids-limit=128','--tmpfs','/run',
                '-v',str(binary)+':/netbird:ro','-v',str(fixture.work/'cert.pem')+':/cert.pem:ro',
                '-e','NB_STATE_DIR=/state','-e','SSL_CERT_FILE=/cert.pem',
                '--entrypoint','/netbird',fixture.helper,'service','run','--log-level','error',
                '--log-file','/run/client.log').decode().strip()
            key=fixture.setup_key()
            fixture.docker('exec','-i',client,'python3','-c',
                'import os,sys; fd=os.open("/run/enroll-key",os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); os.write(fd,sys.stdin.buffer.read());os.close(fd)',input=key.encode())
            try:
                fixture.docker('exec',client,'/netbird','up','--management-url',fixture.url,
                               '--setup-key-file','/run/enroll-key',timeout=120)
            finally:fixture.docker('exec',client,'rm','-f','/run/enroll-key')
            first=connected(fixture,client)
            scan='''from pathlib import Path
import sys
key=sys.stdin.buffer.read()
assert not Path('/run/enroll-key').exists()
paths=list(Path('/state').rglob('*'))+[Path('/run/client.log')]
assert all(key not in path.read_bytes() for path in paths if path.is_file())
'''
            fixture.docker('exec','-i',client,'python3','-c',scan,input=key.encode())
            # Restart the actual daemon/container using only its persistent peer
            # state. /run is tmpfs and the one-use setup key is already deleted.
            fixture.docker('stop','--timeout','30',client)
            fixture.docker('start',client)
            second=connected(fixture,client)
            if first['publicKey']!=second['publicKey']:raise ValueError('Persistent peer identity changed')
            fixture.docker('exec','-i',client,'python3','-c',scan,input=key.encode())
            key=None
            evidence={'kind':'container-netbird-smoke','architecture':args.architecture,
                'client_version':lock['version'],'client_asset_sha256':asset['sha256'],
                'management_image':fixture.image,'helper_image':fixture.helper,
                'real_enrollment':'PASS','management_and_signal':'PASS','identity_after_daemon_restart':'PASS',
                'setup_key_absence_in_client_state_and_log':'PASS','guest_config_commit_save_reboot':'NOT_RUN',
                'guest_image_acceptance':'NOT_RUN','oci':'NOT_RUN'}
            (root/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
        finally:
            if client:
                label=fixture.docker('inspect','--format','{{index .Config.Labels "'+LABEL+'"}}',client).decode().strip()
                if label!=fixture.nonce:raise RuntimeError('Refusing to remove an unowned client')
                fixture.docker('rm','-f',client)
    print('PASS: actual NetBird registration, daemon restart, persistent identity and one-use key absence; guest OS acceptance remains NOT_RUN')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--architecture',required=True,choices=['amd64','arm64'])
    parser.add_argument('--helper-image',required=True)
    parser.add_argument('--work',required=True,type=Path)
    verify(parser.parse_args())
