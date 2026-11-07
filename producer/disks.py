"""Standalone disk conversion from a stopped, freshly installed source disk."""
import json
from pathlib import Path
import subprocess

CAPACITY = 50 * 1024**3


def run(*args):
    subprocess.run(args, check=True)


def info(path):
    return json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(path)]))


def convert(source, destination):
    source, destination = Path(source), Path(destination)
    original = info(source)
    if original.get('virtual-size') != CAPACITY or original.get('backing-filename'):
        raise ValueError('Source must be a standalone stopped 50 GiB fresh install')
    destination.mkdir(parents=True, exist_ok=False)
    outputs = [('disk.qcow2', 'qcow2', ['-c']),
               ('disk.vmdk', 'vmdk', ['-o', 'subformat=streamOptimized'])]
    for name, format_name, options in outputs:
        target = destination / name
        run('qemu-img', 'convert', '-f', original['format'], '-O', format_name,
            *options, str(source), str(target))
        metadata = info(target)
        if (metadata.get('format') != format_name or metadata.get('virtual-size') != CAPACITY
                or metadata.get('backing-filename') or metadata.get('full-backing-filename')):
            raise ValueError('Export format/capacity/standalone audit failed')
        if format_name == 'vmdk':
            detail = metadata.get('format-specific', {}).get('data', {})
            if detail.get('create-type') != 'streamOptimized':
                raise ValueError('VMDK is not streamOptimized')
        run('qemu-img', 'check', '-f', format_name, str(target))
        run('qemu-img', 'compare', '-f', original['format'], '-F', format_name,
            str(source), str(target))
    # Boot tests must use disposable copies; these stopped exports are immutable.
    return [destination / name for name, _, _ in outputs]
