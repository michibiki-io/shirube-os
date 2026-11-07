#!/usr/bin/python3
"""Repack a stopped-candidate root from a private QEMU guest; never run on a host."""
from pathlib import Path
import os
import subprocess
import sys
sys.path.insert(0,'/tmp/shirube-export')
from sanitize import sanitize


def main():
    if os.geteuid()!=0 or not Path('/run/shirube-private-export-session').is_file():
        raise ValueError('Private producer guest session required')
    from vyos.system.disk import find_persistence
    persistence=Path(find_persistence())
    images=list((persistence/'boot').glob('*/*.squashfs'))
    if len(images)!=1:raise ValueError('Expected one fresh installed root filesystem')
    root=Path('/run/shirube-export-root');root.mkdir()
    subprocess.run(['mount','--bind','/',str(root)],check=True)
    try:
        # Keep the live session account until the guest powers off. The final
        # offline pass removes all test accounts before exporting.
        passwd=root/'etc/passwd'
        original=passwd.read_bytes()
        sanitize(root,sys.argv[1])
        passwd.write_bytes(original)
        image=images[0];temporary=image.with_suffix('.sanitized')
        subprocess.run(['mksquashfs',str(root),str(temporary),'-noappend','-comp','xz',
                        '-processors','2','-mem','512M','-no-progress'],check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        os.replace(temporary,image)
        os.sync()
    finally:
        subprocess.run(['umount',str(root)],check=True)
        root.rmdir()
    subprocess.run(['systemd-run','--unit=shirube-export-poweroff','--on-active=3s','/usr/bin/systemctl','poweroff'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    print('SHIRUBE_ROOT_REPACKED')


if __name__=='__main__':main()
