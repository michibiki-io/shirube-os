"""Apply independently owned product identity without renaming VyOS interfaces."""
from pathlib import Path
from .sanitize import path_in_root
import re
import shutil
import subprocess
import hashlib
import json
from .policy import version_parts

DESCRIPTION = 'An open-source router OS based on VyOS Stream, built and maintained by michibiki-io.'


def boot_background(logo, target):
    # Fit the supplied 824x320 artwork onto the boot menu's 640x480 canvas.
    subprocess.run(['rsvg-convert','--background-color=#ffffff','--width','480','--height','480',
        '--keep-aspect-ratio','--page-width','640','--page-height','480','--left','80','--top','20',
        '--output',str(target),str(logo)],check=True)
    import struct
    if struct.unpack('>II',Path(target).read_bytes()[16:24])!=(640,480):
        raise ValueError('Unexpected boot artwork dimensions')


def prepare_source_artwork(sources, logo, license_file):
    """Replace restricted boot assets before compiling or exporting sources."""
    root=Path(sources)/'vyos-build/vyos-build';records=[]
    for member in ['data/live-build-config/bootloaders/grub-pc/splash.png',
                   'data/live-build-config/includes.binary/isolinux/splash.png']:
        target=root/member
        if not target.is_file() or target.is_symlink():raise ValueError('Expected upstream boot artwork missing')
        previous=hashlib.sha256(target.read_bytes()).hexdigest()
        boot_background(logo,target)
        records.append({'path':member,'original_sha256':previous,
                        'replacement_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'license':'MIT'})
    changed=[]
    for member in ['data/live-build-config/bootloaders/grub-pc/grub.cfg',
                   'data/live-build-config/bootloaders/grub-pc/live-theme/theme.txt']:
        path=root/member;before=path.read_text();text=before
        if path.suffix=='.cfg':
            text=text.replace('set color_normal=light-gray/black','set color_normal=black/white').replace('set color_highlight=white/black','set color_highlight=white/blue')
        else:
            palette={'title-color':'#172b3b','title-text':'Shirube OS Live Boot Menu','color':'#172b3b',
                'item_color':'#172b3b','selected_item_color':'#087f73','fg_color':'#14b8a6',
                'bg_color':'#e5e7eb','border_color':'#94a3b8'}
            text=re.sub(r'(?m)^([ \t]*)([\w-]+)(\s*[:=]\s*)"[^"]*"',
                lambda match:match[1]+match[2]+match[3]+'"'+palette[match[2]]+'"' if match[2] in palette else match[0],text)
        path.write_text('# Modified by michibiki-io for Shirube OS on 2026-11-08.\n'+text)
        changed.append({'path':member,'original_sha256':hashlib.sha256(before.encode()).hexdigest(),
                        'replacement_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    (root/'SHIRUBE-ARTWORK.json').write_text(json.dumps({'modified_by':'michibiki-io','date':'2026-11-08',
        'supplied_svg_sha256':hashlib.sha256(Path(logo).read_bytes()).hexdigest(),'replacements':records,
        'boot_style_modifications':changed},indent=2)+'\n')
    shutil.copyfile(license_file,root/'SHIRUBE-ARTWORK-LICENSE')


def install(root, version, logo):
    root, logo = Path(root), Path(logo)
    version_parts(version)
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
    boot_background(logo,destination/'boot-background.png')
    (destination / 'origin.txt').write_text(DESCRIPTION + '\nBased on VyOS Stream 2026.03; independent, unaffiliated distribution.\n')
