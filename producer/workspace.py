"""Retire owned compiler scratch after verified source export, before disk tests."""
from pathlib import Path
import shutil


def retire_compiler_scratch(root, names):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Owned compiler workspace required')
    paths = []
    protected = {'package-output', 'release-rootfs', 'disk', 'source-set-restoration'}
    for name in names:
        if (not isinstance(name, str) or not name or name in {'.', '..'}
                or Path(name).name != name or name in protected):
            raise ValueError('Unsafe compiler scratch selection')
        path = root/name
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError('Compiler scratch must be an owned directory')
        if path.exists():
            paths.append(path)
    # Validate every selection before removing anything. Nested symlinks are
    # unlinked by rmtree; their targets and mounted public caches stay untouched.
    before = shutil.disk_usage(root).free
    for path in paths:
        shutil.rmtree(path)
    return {'kind': 'retired-compiler-scratch', 'directories': [p.name for p in paths],
            'free_bytes_before': before, 'free_bytes_after': shutil.disk_usage(root).free,
            'scope': 'Owned compiler scratch only, after complete source restoration and license clearance'}
