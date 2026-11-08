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


def runtime_displays(root):
    """Brand the native producers of banners and boot progress messages."""
    root=Path(root)
    banner=root/'usr/libexec/vyos/conf_mode/system_login_banner.py'
    text=banner.read_text()
    if text.count('Welcome to VyOS -')!=1:raise ValueError('Unexpected native login banner')
    text=text.replace('Welcome to VyOS -','Welcome to Shirube OS -')
    first,rest=text.split('\n',1)
    banner.write_text(first+'\n# Modified by michibiki-io for Shirube OS on 2026-11-09.\n'+rest)
    router=root/'usr/libexec/vyos/init/vyos-router'
    lines=router.read_text().splitlines(True)
    router.write_text(lines[0]+'# Modified by michibiki-io for Shirube OS on 2026-11-09.\n'+''.join(
        line.replace('VyOS','Shirube OS') if line.lstrip().startswith('log_') else line for line in lines[1:]))
    for template in (root/'usr/share/vyos/templates/login').glob('*.j2'):
        if template.name=='default_motd.j2':continue
        template.write_text(''.join(line if 'copyright' in line.lower() else line.replace('VyOS','Shirube OS')
            for line in template.read_text().splitlines(True)))
    motd=root/'usr/share/vyos/templates/login/default_motd.j2'
    motd.write_text('{# Modified by michibiki-io for Shirube OS on 2026-11-09. #}\n'
        'Shirube OS {{ version_data.version }}\n\n'+DESCRIPTION+'\n\n'
        'Documentation: {{ version_data.documentation_url }}\n'
        'Bug reports: {{ version_data.bugtracker_url }}\n\n'
        'Upstream component copyrights and licenses: /usr/share/doc/*/copyright\n')


def boot_menus(root, architecture, version):
    """Brand generated GRUB entries after live-build expands its placeholders."""
    version_parts(version)
    if architecture not in {'amd64','arm64'}:raise ValueError('Unsupported boot-menu architecture')
    serial='ttyAMA0' if architecture=='arm64' else 'ttyS0'
    changed=0
    pattern=re.compile(r'(?m)^menuentry (["\'])(.*?)\1([^\n]*)\{\n(.*?)^}',re.S)
    for path in (Path(root)/'boot/grub').rglob('*.cfg'):
        if path.is_symlink():raise ValueError('Boot configuration must be a regular file')
        text=path.read_text()
        # ARM's generic expansion can duplicate the local-console arguments in
        # its final normal entry. Make that entry explicitly select PL011.
        normal=[entry for entry in pattern.finditer(text)
                if re.search(r'(?m)^\s*linux\s+/live/vmlinuz',entry[4]) and 'nomodeset' not in entry[4]]
        arm_serial=normal[-1].start() if architecture=='arm64' and normal else None
        def replace(match):
            nonlocal changed
            body=match[4]
            if not re.search(r'(?m)^\s*linux\s+/live/vmlinuz',body):return match[0]
            if match.start()==arm_serial:
                body=re.sub(r'(?m)^(\s*linux\s+/live/vmlinuz[^\n]*)$',
                    lambda line:re.sub(r'[ \t]+console=[^ \t\r\n]+','',line[0]).rstrip()+' console=tty0 console=ttyAMA0,115200',body)
            consoles=re.findall(r'(?:^|\s)console=([^,\s]+)',body)
            if not consoles or consoles[-1] not in {'tty0',serial}:
                raise ValueError('Unexpected generated live-console selection')
            console='serial console' if consoles[-1]==serial else 'local console'
            safe='fail-safe' in match[2] or 'nomodeset' in body
            title='Shirube OS '+version+' Live ('+console+(', safe mode' if safe else '')+')'
            attributes=match[3]
            if match.start()==arm_serial and '--id=shirube-live-serial' not in attributes:
                if '--id=' in attributes:raise ValueError('Unexpected ARM live entry identifier')
                attributes+=' --id=shirube-live-serial '
            if console=='serial console' and not safe:
                if '--hotkey=' in attributes:
                    attributes=re.sub(r'--hotkey=\S+','--hotkey=s',attributes)
                else:attributes+=' --hotkey=s '
            body=body.replace('hostname=vyos','hostname=shirube-os')
            changed+=1
            return 'menuentry "'+title+'"'+attributes+'{\n'+body+'}'
        updated=pattern.sub(replace,text)
        if arm_serial is not None:
            updated,count=re.subn(r'(?m)^set default=.*$','set default=shirube-live-serial',updated,count=1)
            if count!=1:raise ValueError('Missing ARM live default selection')
        if updated!=text:
            notice='# Modified by michibiki-io for Shirube OS on 2026-11-09.\n'
            path.write_text(updated if notice in updated else notice+updated)
    if not changed:raise ValueError('No generated live GRUB entries were branded')
    return changed


def defer_factory_identity(root):
    """Keep base-files-owned paths out of the core DEB payload.

    Live-build applies these templates after package installation, like its
    generated os-release, without claiming another package's files.
    """
    root = Path(root)
    destination = root / 'usr/share/shirube/factory-identity'
    destination.mkdir()
    for name in ('os-release', 'issue', 'issue.net'):
        source = root / 'etc' / name
        if source.is_symlink() or not source.is_file():
            raise ValueError('Expected generated regular factory identity file')
        source.replace(destination / name)


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
