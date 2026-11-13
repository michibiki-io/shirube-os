"""Preferred source selection; shared with final source collection."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
from .sources import sha256
from .source_selectors import files, tracked_source_files


def kea_source_files(root, preferred, packaging, tool):
    """Retain original Kea plus actual prepared recipes, excluding build trees."""
    root,preferred,packaging=map(Path,[root,preferred,packaging])
    if (root/'shirube-stage-source.json').is_file():
        yield from tracked_source_files(root)
        return
    yield from files(preferred,exclude=('debian','build'))
    names=subprocess.check_output(['git','-C',str(packaging),'ls-files','-z','--','debian'],text=True).split('\0')
    recipes=set(filter(None,names))|{'debian/not-installed'}
    for member in sorted(recipes):
        relative=Path(member)
        if relative.is_absolute() or '..' in relative.parts or relative.parts[0]!='debian':
            raise ValueError('Unsafe Kea packaging recipe')
        actual=root/relative
        # The build deliberately removes premium/subscriber/doc install lists.
        if not actual.exists():continue
        if actual.is_symlink() or not actual.is_file():raise ValueError('Nonregular Kea packaging recipe')
        yield actual,member
    for member in ['debian/rules','debian/control','debian/changelog','debian/not-installed']:
        if member not in recipes or not (root/member).is_file():raise ValueError('Missing prepared Kea recipe')
    member='shirube-corresponding-inputs/meson-'+tool['version']+'.tar.gz'
    archive=root/member
    if archive.is_symlink() or not archive.is_file() or sha256(archive)!=tool['sha256']:
        raise ValueError('Original Kea Meson build source differs')
    yield archive,member
