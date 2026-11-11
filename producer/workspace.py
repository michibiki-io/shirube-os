"""Retire owned compiler scratch after verified source export, before disk tests."""
from pathlib import Path
import re
import shutil


def _selections(root, names):
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
    return paths


def _mount_points():
    # st_dev alone does not identify bind mounts on the same filesystem.
    return [Path(re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)),
                        line.split()[4]))
            for line in Path('/proc/self/mountinfo').read_text().splitlines()]


def _retire(groups):
    selected = [(label, root, _selections(root, names))
                for label, root, names in groups]
    mounts = _mount_points()
    for _, _, paths in selected:
        for path in paths:
            absolute = path.absolute()
            if any(mount == absolute or mount.is_relative_to(absolute) for mount in mounts):
                raise ValueError('Compiler scratch contains a mounted resource')
    # Validate all workspaces before removing any. Nested symlinks are unlinked
    # without following their targets; mounted caches are rejected above.
    root = selected[0][1]
    before = shutil.disk_usage(root).free
    for _, _, paths in selected:
        for path in paths:
            shutil.rmtree(path)
    return {'kind': 'retired-compiler-scratch',
            'directories': [p.name for _, _, paths in selected for p in paths],
            'workspaces': {label: [p.name for p in paths] for label, _, paths in selected},
            'free_bytes_before': before, 'free_bytes_after': shutil.disk_usage(root).free,
            'scope': 'Owned compiler scratch only, after complete source restoration and license clearance'}


def retire_compiler_scratch(root, names):
    return _retire([('packages', Path(root), names)])


def retire_native_scratch(work, jobs, *, filesystem_root=Path('/')):
    """Release task-owned compiler/assembly trees before the 50 GiB disk phase."""
    root = Path(filesystem_root)
    groups = [('packages', Path(work), list(jobs) +
               ['sources', 'input', 'stream', 'dependencies', 'corresponding-source'])]
    for label, directory, names in [
        ('extra-components', 'extra', ['base', 'vpp', 'go', 'kea', 'salt', 'podman', 'modules', 'accel']),
        ('assembly', 'iso-work', ['repo', 'vyos-build', 'vyos-1x', 'verify']),
        ('compiler-cache', 'root', ['.cache', 'go']),
        ('netbird-build', 'guest-package', ['netbird-source-build', 'netbird-correspondence']),
    ]:
        path = root / directory
        if path.exists() or path.is_symlink():
            groups.append((label, path, names))
    return _retire(groups)
