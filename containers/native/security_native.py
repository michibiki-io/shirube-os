"""Component-scoped, offline security backports for authenticated Stream sources.

Copyright (c) 2026 michibiki.io; MIT License (see NOTICE).
"""
import argparse
from pathlib import Path


try:
    from .security_common import patch_root, retain, run_patch
except ImportError:
    from security_common import patch_root, retain, run_patch


def apply(project, component, source):
    if component != 'update-check':
        raise ValueError('Unknown native security component')
    source = Path(source)
    root = patch_root(project)/component
    patches = sorted(root.glob('*.patch'))
    if not patches:
        raise ValueError('Missing production security patches')
    # Check the whole patch before mutation. Combined overlapping histories avoid
    # accepting an intermediate fix as the final already-applied state.
    for patch in patches:
        if run_patch(source, patch, dry=True).returncode == 0:
            result = run_patch(source, patch)
            if result.returncode:
                raise ValueError('Security patch application failed: '+patch.name)
        elif run_patch(source, patch, reverse=True, dry=True).returncode != 0:
            raise ValueError('Unexpected or partial security source state: '+patch.name)
    return retain(project, component, source, Path(__file__))

def prepare_frr(project, source):
    """Append complete fixes before library identity/context/restore.

    Leave archived .pc and applied Stream hunks alone: both existing workers
    reverse/normalize the entire quilt series before dpkg-source applies it.
    """
    source = Path(source)
    directory = source/'debian/patches'
    series = directory/'series'
    if not series.is_file():
        raise ValueError('Missing archived FRR quilt series')
    originals = sorted((patch_root(project)/'frr').glob('*.patch'))
    for patch in originals:
        if (run_patch(source, patch, dry=True).returncode != 0
                and run_patch(source, patch, reverse=True, dry=True).returncode != 0):
            raise ValueError('Unexpected or partial FRR security source state: '+patch.name)
    expected = ['shirube-security-'+p.name for p in originals]
    text = series.read_text()
    names = [line.split()[0] for line in text.splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    present = [name for name in names if name.startswith('shirube-security-')]
    if present and present != expected:
        raise ValueError('Unexpected FRR security quilt series')
    for name, patch in zip(expected, originals):
        target = directory/name
        if target.exists() and (target.is_symlink() or target.read_bytes() != patch.read_bytes()):
            raise ValueError('Different FRR security quilt patch')
        target.write_bytes(patch.read_bytes())
    if not present:
        series.write_text(text + ('' if text.endswith('\n') else '\n') + '\n'.join(expected)+'\n')
    return retain(project, 'frr', source, Path(__file__), state='quilt-prepared')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=['frr', 'update-check'])
    parser.add_argument('source', type=Path)
    parser.add_argument('--project', type=Path, default=Path('/src'))
    args = parser.parse_args()
    if args.component == 'frr':
        prepare_frr(args.project, args.source)
    else:
        apply(args.project, args.component, args.source)

if __name__ == '__main__': main()
