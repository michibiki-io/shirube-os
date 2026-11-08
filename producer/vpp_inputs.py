"""Preseed the pinned upstream DPDK Python bootstrap without latest resolution."""
from pathlib import Path
import re
import shutil
from .sources import download,sha256


def prepare_external(lock,destination,cache=None):
    """Preseed native archives over HTTPS and check their upstream hashes.

    A proxy error page must never become an upstream build input. The ordinary
    downloader retries transient errors at the same fixed URL only.
    """
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    seen=set()
    if not lock.get('inputs'):raise ValueError('Missing VPP native source inputs')
    for record in lock['inputs']:
        name=record['filename'];digest=record['sha256']
        if (not re.fullmatch(r'[A-Za-z0-9_.+-]+',name) or not re.fullmatch('[0-9a-f]{64}',digest)
                or name in seen or not record['url'].startswith('https://')):
            raise ValueError('Invalid fixed VPP native source input')
        seen.add(name);target=destination/name
        if target.is_symlink():raise ValueError('Unexpected VPP native source link')
        if not target.exists():
            original=Path(cache)/name if cache else None
            if original is not None and original.is_file() and not original.is_symlink():
                shutil.copy2(original,target)
            else:download(record['url'],target)
        if not target.is_file() or sha256(target)!=digest:
            raise ValueError('VPP native source input hash mismatch')


def prepare(lock,destination,cache=None):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    expected=set()
    for record in lock['inputs']:
        name=record['filename'];digest=record['sha256']
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+',name) or not re.fullmatch('[0-9a-f]{64}',digest):
            raise ValueError('Invalid fixed VPP bootstrap input')
        if name in expected:raise ValueError('Duplicate VPP bootstrap input')
        expected.add(name);target=destination/name
        if target.is_symlink():raise ValueError('Unexpected VPP bootstrap source link')
        if not target.exists():
            original=Path(cache)/name if cache else None
            if original is not None and original.is_file() and not original.is_symlink():
                shutil.copy2(original,target)
            else:download(record['url'],target)
        if sha256(target)!=digest:raise ValueError('VPP bootstrap input hash mismatch')
    if not any(name.startswith('meson-0.57.2.') for name in expected):
        raise ValueError('Pinned upstream Meson bootstrap missing')
    # Prevent pip from selecting an unrecorded wheel left by an earlier attempt.
    actual={path.name for path in destination.iterdir() if path.suffix=='.whl' or path.name.startswith('meson-')}
    if actual!=expected:raise ValueError('Unexpected Python bootstrap input in VPP cache')
