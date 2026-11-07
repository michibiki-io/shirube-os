"""Apply independently owned product identity without renaming VyOS interfaces."""
from pathlib import Path
from .sanitize import path_in_root
import re
import shutil
import subprocess

DESCRIPTION = 'An open-source router OS based on VyOS Stream, built and maintained by michibiki-io.'


def install(root, version, logo):
    root, logo = Path(root), Path(logo)
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Independent project semantic version required')
    if b'<svg ' not in logo.read_bytes() or b'Shirube OS' not in logo.read_bytes():
        raise ValueError('Expected repository-supplied Shirube OS SVG')
    identity = path_in_root(root, 'etc/os-release')
    if identity.is_symlink():
        identity.unlink()
    identity.parent.mkdir(parents=True, exist_ok=True)
    identity.write_text(f'NAME="Shirube OS"\nID=shirube-os\nID_LIKE="vyos debian"\nVERSION_ID="{version[1:]}"\nPRETTY_NAME="Shirube OS {version}"\nHOME_URL="https://github.com/michibiki-io/shirube-os"\nSUPPORT_URL="https://github.com/michibiki-io/shirube-os/issues"\nBUG_REPORT_URL="https://github.com/michibiki-io/shirube-os/issues"\n')
    for name in ('issue', 'issue.net'):
        path_in_root(root, 'etc/'+name).write_text(f'Shirube OS {version} — built by michibiki-io\n')
    destination = path_in_root(root, 'usr/share/shirube')
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(logo, destination / 'logo.svg')
    # GRUB uses PNG. Conversion stays in the image-building container.
    subprocess.run(['rsvg-convert', '--background-color=#ffffff', '--output',
                    str(destination / 'logo.png'), str(logo)], check=True)
    (destination / 'origin.txt').write_text(DESCRIPTION + '\nBased on VyOS Stream 2026.03; independent, unaffiliated distribution.\n')
