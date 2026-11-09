"""Preserve package binary/source bindings through upstream image assembly."""
import json
from pathlib import Path, PurePosixPath
import re


def preserve_runtime_bindings(hook, extra):
    paths = set()
    for mapping in Path(extra).glob('*/*/shirube-corresponding-inputs/binary-info.json'):
        for record in json.loads(mapping.read_text()):
            member = record['path']
            path = PurePosixPath(member)
            if (not re.fullmatch(r'usr/[A-Za-z0-9_./+\-]+', member)
                    or '..' in path.parts or str(path) != member):
                raise ValueError('Unsafe retained runtime binary path')
            paths.add('/'+member)
    if not paths:
        raise ValueError('Missing native runtime binary bindings')
    hook = Path(hook)
    text = hook.read_text()
    marker = "STRIP_EXCLUDE=`dpkg-query -L libbinutils | grep '.so'`"
    if text.splitlines().count(marker) != 1:
        raise ValueError('Unexpected upstream binary stripping policy')
    text = text.replace(marker, marker+'\n# Keep exact source-bound package binaries unchanged.\n'
                        'STRIP_EXCLUDE="${STRIP_EXCLUDE}\n'+'\n'.join(sorted(paths))+'"')
    hook.write_text(text)

