"""Preferred source selection; shared with final source collection."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
from .sources import sha256
GENERATED={'__pycache__', '.debhelper', '.pytest_cache', '.git', 'shirube-stage-source.json'}
SUFFIXES={'.cmx', '.pyc', '.so', '.cmo', '.ko', '.o', '.deb', '.a', '.cmi'}


def files(root, exclude=()):
    root=Path(root).resolve()
    excluded=[Path(member) for member in exclude]
    def omitted(relative):
        return any(relative==member or member in relative.parents for member in excluded)
    for directory,children,names in os.walk(root,followlinks=False):
        base=Path(directory)
        children[:]=sorted(name for name in children if name not in GENERATED
                           and not omitted((base/name).relative_to(root)))
        for name in sorted(names):
            path=base/name;relative=path.relative_to(root)
            if name in GENERATED or omitted(relative):continue
            if name.startswith('.') and name.endswith('.cmd'):continue
            if name in {'signing_key.pem','vmlinux','System.map'} or path.suffix in SUFFIXES:continue
            if path.is_file():
                # os.walk does not enter directory symlinks. Resolve file
                # aliases only, avoiding repeated ancestor lookups per file.
                resolved=path.resolve() if path.is_symlink() else path
                if not resolved.is_relative_to(root):raise ValueError('Source symlink escapes component')
                yield resolved,str(relative)


def tracked_source_files(root):
    """Export patched tracked source, materializing confined directory aliases."""
    root=Path(root).resolve()
    if (root/'shirube-stage-source.json').is_file():
        from .stage_artifacts import member_name
        members=json.loads((root/'shirube-stage-source.json').read_text())['members']
        if not members or len(members)!=len(set(members)):raise ValueError('Incomplete staged source member set')
        for name in members:
            path=root/member_name(name)
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                raise ValueError('Staged preferred source member missing/unsafe')
            yield path,name
        return
    names=subprocess.check_output(['git','-C',str(root),'ls-files','-z']).decode().split('\0')
    for name in sorted(filter(None,names)):
        path=Path(name)
        if path.is_absolute() or '..' in path.parts or '.git' in path.parts:
            raise ValueError('Unsafe tracked source path')
        source=root/path
        if source.is_symlink():
            if not source.resolve().is_relative_to(root):raise ValueError('External VPP source link')
            if source.is_dir():
                # Materialize this confined directory alias from the same
                # tracked tree; archives intentionally contain regular files.
                prefix=source.resolve().relative_to(root)
                for tracked in sorted(filter(None,names)):
                    try:relative=Path(tracked).relative_to(prefix)
                    except ValueError:continue
                    target=root/tracked
                    if target.is_file():
                        if not target.resolve().is_relative_to(root):raise ValueError('External VPP source link')
                        yield target.resolve(),name+'/'+str(relative)
                continue
        if not source.is_file():raise ValueError('Missing patched tracked source')
        yield source.resolve(),name
