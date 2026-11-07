#!/usr/bin/python3
"""Prepare credential-free factory defaults inside the live-build chroot only."""
import os
from pathlib import Path
import shutil
import sys


def main():
    if os.environ.get('SHIRUBE_BUILD_CHROOT')!='1' or not Path('/usr/share/shirube/logo.svg').is_file():
        raise ValueError('Factory preparation is restricted to the image build chroot')
    arch=sys.argv[1]
    if arch not in {'amd64','arm64'}:raise ValueError('Unsupported architecture')
    console='ttyAMA0' if arch=='arm64' else 'ttyS0'
    config='interfaces {\n ethernet eth0 {\n  address dhcp\n }\n loopback lo {\n }\n}\nsystem {\n host-name shirube-os\n console {\n  device '+console+' {\n   speed 115200\n  }\n }\n}\n'
    root=Path('/opt/vyatta/etc/config');root.mkdir(parents=True,exist_ok=True)
    for name in ['config.boot','config.boot.default']:(root/name).write_text(config)
    Path('/opt/vyatta/etc/config.boot.default').write_text(config)
    (root/'shirube-first-boot').write_text('pending\n')
    (Path('/usr/share/shirube')/'needs-enrollment').write_text('pending\n')
    shadow=Path('/etc/shadow');lines=[]
    for line in shadow.read_text().splitlines():
        fields=line.split(':')
        if len(fields)!=9:raise ValueError('Unexpected shadow record')
        fields[1]='!';lines.append(':'.join(fields))
    shadow.write_text('\n'.join(lines)+'\n');shadow.chmod(0o600)
    for directory,pattern in [('/etc/ssh','ssh_host_*'),('/etc/ssl/private','*')]:
        for file in Path(directory).glob(pattern):
            if file.is_file() or file.is_symlink():file.unlink()
    machine=Path('/etc/machine-id')
    if machine.is_symlink():machine.unlink()
    machine.write_text('')
    for name in ['/var/lib/dbus/machine-id','/var/lib/systemd/random-seed']:
        file=Path(name)
        if file.exists() or file.is_symlink():file.unlink()


if __name__=='__main__':main()
