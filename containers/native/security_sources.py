"""Component-scoped, offline security backports for authenticated Stream sources.

Copyright (c) 2026 michibiki.io; MIT License (see NOTICE).
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def patch_root(project):
    project = Path(project)
    return project / ('patches/vyos/security' if (project/'containers/native').is_dir()
                      else 'vyos-patches/security')


def inputs(project, component):
    root = patch_root(project)/component
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError('Nonregular security input')
        if path.is_file():
            result[str(path.relative_to(root))] = digest(path)
    if not result:
        raise ValueError('Missing security patch inputs: '+component)
    return result


def retain(project, component, source):
    """Explicit reconstruction inputs survive the preferred-source selectors."""
    destination = Path(source)/'shirube-corresponding-inputs'/('security-'+component)
    expected = inputs(project, component)
    recipe = Path(__file__).read_bytes()
    if destination.exists():
        if (not (destination/'recipe.py').is_file()
                or (destination/'recipe.py').read_bytes() != recipe
                or {str(p.relative_to(destination/'patches')): digest(p)
                    for p in (destination/'patches').rglob('*') if p.is_file()} != expected):
            raise ValueError('Different retained security preparation inputs')
    else:
        destination.mkdir(parents=True)
        shutil.copytree(patch_root(project)/component, destination/'patches')
        (destination/'recipe.py').write_bytes(recipe)
    receipt = {'component': component, 'state': 'quilt-prepared' if component == 'frr' else 'applied',
               'patches': expected,
               'recipe_sha256': hashlib.sha256(recipe).hexdigest()}
    (destination/'receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2)+'\n')
    return receipt


def run_patch(source, patch, *, reverse=False, dry=False):
    command = ['patch', '--batch', '--fuzz=0', '--no-backup-if-mismatch', '-p1',
               '--reverse' if reverse else '--forward']
    if dry:
        command.append('--dry-run')
    return subprocess.run(command, input=Path(patch).read_bytes(), cwd=source,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def apply(project, component, source):
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
            return retain(project, component, source)
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
    return retain(project, component, source)


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
    return retain(project, 'frr', source)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=['accel-ppp', 'update-check'])
    parser.add_argument('source', type=Path)
    parser.add_argument('--project', type=Path, default=Path('/src'))
    args = parser.parse_args()
    apply(args.project, args.component, args.source)


if __name__ == '__main__':
    main()
