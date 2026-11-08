"""Use the task-built kernel for container-contained offline disk inspection."""
from contextlib import contextmanager
import gzip
import os
from pathlib import Path
import subprocess
from .architecture import check_kernel
from .sources import sha256


@contextmanager
def appliance_kernel(packages, architecture, work, accelerator='kvm'):
    """Select matching kernel/modules without requiring the host's /boot.

    Supermin's custom-kernel interface is documented at
    https://libguestfs.org/supermin.1.html#USING-A-CUSTOM-KERNEL-AND-KERNEL-MODULES
    Every invocation has its own appliance cache and restores process settings.
    """
    if architecture not in {'amd64', 'arm64'}:
        raise ValueError('Unsupported appliance architecture')
    if accelerator not in {'kvm','tcg'}:raise ValueError('Unsupported appliance accelerator')
    if not Path('/lib/modules').is_dir():
        raise ValueError('Producer image lacks the module directory required by supermin')
    work = Path(work)
    work.mkdir()
    candidates = list(Path(packages).glob(f'linux-image-*_{architecture}.deb'))
    if len(candidates) != 1:
        raise ValueError('Exactly one task-built appliance kernel is required')
    package = candidates[0]
    actual = subprocess.check_output(['dpkg-deb', '-f', str(package), 'Architecture'], text=True).strip()
    if actual != architecture:
        raise ValueError('Wrong appliance kernel package architecture')
    root = work/'kernel-root'
    subprocess.run(['dpkg-deb', '-x', str(package), str(root)], check=True)
    kernels = list((root/'boot').glob('vmlinuz-*'))
    if len(kernels) != 1:
        raise ValueError('Expected one packaged appliance kernel')
    kernel = kernels[0]
    version = kernel.name.removeprefix('vmlinuz-')
    modules = root/'lib/modules'/version
    if not modules.is_dir():
        modules = root/'usr/lib/modules'/version
    if not modules.is_dir() or not modules.resolve().is_relative_to(root.resolve()):
        raise ValueError('Matching appliance modules are unavailable')
    data = kernel.read_bytes()
    if architecture == 'arm64' and data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
        kernel = work/'Image'
        kernel.write_bytes(data)
    check_kernel(data, architecture)
    cache = work/'cache'
    cache.mkdir()
    temporary = work/'tmp'
    temporary.mkdir()
    settings = {'SUPERMIN_KERNEL': str(kernel.resolve()),
                'SUPERMIN_MODULES': str(modules.resolve()),
                'SUPERMIN_KERNEL_VERSION': version,
                'LIBGUESTFS_BACKEND': 'direct',
                'LIBGUESTFS_BACKEND_SETTINGS': 'force_'+accelerator,
                'LIBGUESTFS_CACHEDIR': str(cache.resolve()),
                'LIBGUESTFS_TMPDIR': str(temporary.resolve())}
    previous = {name: os.environ.get(name) for name in settings}
    try:
        os.environ.update(settings)
        yield {'kernel_package_sha256': sha256(package), 'kernel_version': version,
               'architecture': architecture, 'accelerator':accelerator}
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
