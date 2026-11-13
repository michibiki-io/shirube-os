"""Component-scoped, offline security backports for authenticated Stream sources.

Copyright (c) 2026 michibiki.io; MIT License (see NOTICE).
"""
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


def retain(project, component, source, recipe_path, *, state="applied"):
    """Explicit reconstruction inputs survive the preferred-source selectors."""
    destination = Path(source)/'shirube-corresponding-inputs'/('security-'+component)
    expected = inputs(project, component)
    recipe = Path(recipe_path).read_bytes()
    companion = Path(__file__).read_bytes()
    retained_inputs = {'recipe.py': hashlib.sha256(recipe).hexdigest(),
                       'security_common.py': hashlib.sha256(companion).hexdigest()}
    if destination.exists():
        if (not (destination/'recipe.py').is_file()
                or (destination/'recipe.py').read_bytes() != recipe
                or not (destination/'security_common.py').is_file()
                or (destination/'security_common.py').read_bytes() != companion
                or {str(p.relative_to(destination/'patches')): digest(p)
                    for p in (destination/'patches').rglob('*') if p.is_file()} != expected):
            raise ValueError('Different retained security preparation inputs')
    else:
        destination.mkdir(parents=True)
        shutil.copytree(patch_root(project)/component, destination/'patches')
        (destination/'recipe.py').write_bytes(recipe)
        (destination/'security_common.py').write_bytes(companion)
    receipt = {'component': component, 'state': state, 'inputs': retained_inputs,
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
