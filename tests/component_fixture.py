"""Real declared inputs for offline scope/transfer fixtures; no compiler execution."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def component_project(destination, *, container=False):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name in ('producer', 'containers/native', 'patches'):
        shutil.copytree(ROOT/name, destination/name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__'))
    for name in ('source-lock.json', 'LICENSE', 'shirube-os-logo.svg', 'NOTICE', 'license-audit.json'):
        shutil.copy2(ROOT/name, destination/name)
    if container:
        (destination/'containers/native').rename(destination/'native')
        (destination/'containers').rmdir()
        (destination/'patches/vyos').rename(destination/'vyos-patches')
        (destination/'patches/nmap').rename(destination/'nmap-patches')
        (destination/'patches/kernel').rename(destination.parent/'kernel-patches')
        (destination/'patches').rmdir()
    return destination
