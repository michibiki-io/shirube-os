"""Component-scoped, offline security backports for authenticated Stream sources.

Copyright (c) 2026 michibiki.io; MIT License (see NOTICE).
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


try:
    from .security_common import digest, patch_root, inputs, retain, run_patch
except ImportError:
    from security_common import digest, patch_root, inputs, retain, run_patch


def apply(project, component, source):
    if component != 'accel-ppp':
        raise ValueError('Unknown kernel security component')
    source = Path(source)
    root = patch_root(project)/component
    patches = sorted(root.glob('*.patch'))
    if not patches:
        raise ValueError('Missing production security patches')
    if component == 'accel-ppp':
        states = json.loads((root/'state.json').read_text())
        def matches(state):
            for name, hashes in states.items():
                path = source/name
                if path.is_symlink():
                    return False
                actual = digest(path) if path.is_file() else None
                if actual != hashes[state]:
                    return False
            return True
        if matches('after'):
            return retain(project, component, source, Path(__file__))
        if not matches('before'):
            raise ValueError('Unexpected or partial accel-ppp security source state')
    # Check the whole patch before mutation. Combined overlapping histories avoid
    # accepting an intermediate fix as the final already-applied state.
    for patch in patches:
        if run_patch(source, patch, dry=True).returncode == 0:
            result = run_patch(source, patch)
            if result.returncode:
                raise ValueError('Security patch application failed: '+patch.name)
        elif run_patch(source, patch, reverse=True, dry=True).returncode != 0:
            raise ValueError('Unexpected or partial security source state: '+patch.name)
    if component == 'accel-ppp' and not matches('after'):
        raise ValueError('Security patch result differs from complete upstream sequence')
    return retain(project, component, source, Path(__file__))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=['accel-ppp'])
    parser.add_argument('source', type=Path)
    parser.add_argument('--project', type=Path, default=Path('/src'))
    args = parser.parse_args()
    apply(args.project, args.component, args.source)

if __name__ == '__main__': main()
