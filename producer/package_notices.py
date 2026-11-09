"""Retain upstream legal files missing from four native package recipes."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess


COMPONENTS = {
    'accel-ppp-ng': ('1.13.0', '/extra/accel/accel'),
    'hsflowd': ('2.1.21-1', '/extra/accel/sflow'),
    'python3-pyhumps': ('3.8.0-1', '/extra/base/humps'),
    'udp-broadcast-relay': ('0.3+vyos', '/extra/base/relay'),
}


def retain(deb, source, work):
    """Repackage legal metadata only; retain upstream terms without approval."""
    deb, source, work = Path(deb), Path(source).resolve(), Path(work)
    name, version = subprocess.check_output(
        ['dpkg-deb', '--show', '--showformat=${Package}\t${Version}', str(deb)],
        text=True).split('\t')
    if name not in COMPONENTS or version != COMPONENTS[name][0]:
        raise ValueError('Unexpected native legal-notice package identity')
    notices = []
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if any(part in {'.git', 'build', 'deb_dist', 'dist', 'package', '_CPack_Packages'}
               for part in relative.parts):
            continue
        if not path.name.lower().startswith(('license', 'licence', 'copying', 'copyright', 'notice', 'authors')):
            continue
        if not path.resolve().is_relative_to(source):
            raise ValueError('Native legal notice escapes preferred source')
        if path.is_file() and path.stat().st_size:
            notices.append((path, relative))
    if not notices:
        raise ValueError('Native component has no original legal notices')
    work.mkdir()
    root = work/'package'
    subprocess.run(['dpkg-deb', '-R', str(deb), str(root)], check=True)
    doc = root/'usr/share/doc'/name
    if doc.is_symlink():
        raise ValueError('Native legal-notice directory must be regular')
    doc.mkdir(parents=True, exist_ok=True)
    copyright_file = doc/'copyright'
    if copyright_file.is_symlink():
        raise ValueError('Native copyright notice must be regular')
    original = copyright_file.read_bytes() if copyright_file.exists() else b''
    records = []
    with copyright_file.open('wb') as target:
        target.write(original)
        target.write(b'\nOriginal upstream notices retained by michibiki-io for Shirube OS.\n'
                     b'This preserves upstream terms; it does not approve or relicense the component.\n')
        for path, relative in notices:
            data = path.read_bytes()
            retained = doc/'upstream-notices'/relative
            retained.parent.mkdir(parents=True, exist_ok=True)
            retained.write_bytes(data)
            target.write(('\nUpstream notice: '+str(relative)+'\n').encode()+data+b'\n')
            records.append({'path': str(relative), 'sha256': hashlib.sha256(data).hexdigest()})
    (doc/'upstream-notices.json').write_text(json.dumps(records, indent=2)+'\n')
    sums = root/'DEBIAN/md5sums'
    if sums.exists():
        sums.write_text(''.join(hashlib.md5(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(root))+'\n'
                              for p in sorted(root.rglob('*')) if p.is_file() and not p.is_symlink()
                              and 'DEBIAN' not in p.relative_to(root).parts))
    result = work/'with-notices.deb'
    subprocess.run(['dpkg-deb', '--root-owner-group', '-b', str(root), str(result)], check=True)
    shutil.copystat(deb, result)
    os.replace(result, deb)


def retain_all(debs, work):
    found = {}
    for deb in Path(debs).glob('*.deb'):
        name = subprocess.check_output(['dpkg-deb', '-f', str(deb), 'Package'], text=True).strip()
        if name in COMPONENTS:
            if name in found:
                raise ValueError('Ambiguous native legal-notice package')
            found[name] = deb
    if set(found) != set(COMPONENTS):
        raise ValueError('Missing native legal-notice package')
    Path(work).mkdir()
    for name, deb in found.items():
        retain(deb, COMPONENTS[name][1], Path(work)/name)
