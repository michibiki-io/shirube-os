"""Retain explicitly hash-bound embedded upstream notices without guessing licenses."""
import hashlib
import json
from pathlib import Path

from .sources import sha256


def retain(source, name, lock_path, common=Path('/usr/share/common-licenses'),
           kernel=Path('/build-private/kernel/source/LICENSES')):
    source = Path(source).resolve()
    lock = json.loads(Path(lock_path).read_text())
    declaration = lock['embedded_legal_notices'][name]
    if declaration['stream_sha256'] != lock['stream']['sha256']:
        raise ValueError('Embedded notice source baseline differs')
    parts = [b'Original upstream notices retained for Shirube OS packaging.\n'
             b'This file does not grant a license or relicense upstream sources.\n']
    seen = set()
    for record in declaration['files']:
        relative = Path(record['path'])
        if (relative.is_absolute() or '..' in relative.parts or '\\' in str(relative)
                or str(relative) != record['path'] or str(relative) in seen):
            raise ValueError('Unsafe or duplicate embedded notice path')
        seen.add(str(relative))
        path = source / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source):
            raise ValueError('Embedded notice source is missing or linked')
        if path.stat().st_size > 4 * 1024**2 or sha256(path) != record['sha256']:
            raise ValueError('Embedded notice source hash differs')
        length = record['prefix_bytes']
        if type(length) is not int or not 0 < length <= path.stat().st_size:
            raise ValueError('Invalid embedded notice extent')
        with path.open('rb') as stream:
            content = stream.read(length)
        if hashlib.sha256(content).hexdigest() != record['notice_sha256']:
            raise ValueError('Embedded legal text hash differs')
        parts.append(('\nOriginal source: ' + str(relative) + '\n').encode() + content + b'\n')
    for record in declaration['full_licenses']:
        kind, member = record['source'].split(':', 1)
        relative = Path(member)
        if relative.is_absolute() or '..' in relative.parts or '\\' in member:
            raise ValueError('Unsafe full license path')
        root = {'common': Path(common), 'kernel': Path(kernel)}[kind].resolve()
        path = root / relative
        if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root)
                or path.stat().st_size > 4 * 1024**2 or sha256(path) != record['sha256']):
            raise ValueError('Full upstream license missing or differs')
        parts.append(('\nFull upstream license: ' + record['source'] + '\n').encode()
                     + path.read_bytes() + b'\n')
    target = source / 'NOTICE.shirube-upstream'
    with target.open('xb') as output:
        output.write(b''.join(parts))
    return target
