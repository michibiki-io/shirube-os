"""Native architecture, runner labels and binary header validation."""
import json
import platform
import re
from pathlib import Path

ARCHITECTURES = {
    'amd64': {'aliases': {'amd64', 'x86_64'}, 'runner': 'ubuntu-24.04',
              'elf': 62, 'pe': 0x8664, 'efi': 'BOOTX64.EFI',
              'console': 'ttyS0', 'qemu': 'qemu-system-x86_64', 'machine': 'q35'},
    'arm64': {'aliases': {'arm64', 'aarch64'}, 'runner': 'ubuntu-24.04-arm',
              'elf': 183, 'pe': 0xaa64, 'efi': 'BOOTAA64.EFI',
              'console': 'ttyAMA0', 'qemu': 'qemu-system-aarch64', 'machine': 'virt'},
}


def native(architecture, actual=None):
    if architecture not in ARCHITECTURES:
        raise ValueError('Supported architectures: amd64, arm64')
    if (actual or platform.machine()) not in ARCHITECTURES[architecture]['aliases']:
        raise ValueError('A native runner for the selected architecture is required')
    return ARCHITECTURES[architecture]


def runner(architecture, setting=''):
    """Return runs-on compatible labels, without online/provider fallback."""
    default = ARCHITECTURES[architecture]['runner']
    setting = setting.strip()
    if not setting:
        return default
    if setting.startswith('['):
        labels = json.loads(setting)
        if not isinstance(labels, list) or not labels or any(
                not isinstance(x, str) or not x.strip() for x in labels):
            raise ValueError('Runner JSON must be a nonempty array of labels')
        return labels
    if not re.fullmatch(r'[A-Za-z0-9_. -]+', setting):
        raise ValueError('Runner must be a label or JSON array of labels')
    return setting


def release_accelerator(architecture, setting='kvm'):
    if architecture not in ARCHITECTURES or setting not in {'kvm','tcg'}:
        raise ValueError('Explicit supported native architecture/QEMU accelerator required')
    if setting=='tcg' and architecture!='amd64':
        raise ValueError('ARM64 TCG release acceptance is incomplete; use a validated KVM runner')
    return setting


def container_runtime(setting='', *, registered=False):
    """An explicit per-container selection never changes the daemon default."""
    if setting and not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*',setting):
        raise ValueError('Invalid scoped Docker runtime name')
    if setting and registered:
        import subprocess
        try:
            available=json.loads(subprocess.check_output(['docker','info','--format','{{json .Runtimes}}'],
                                                        text=True,stderr=subprocess.DEVNULL))
        except (OSError,subprocess.CalledProcessError,json.JSONDecodeError):
            raise ValueError('Could not inspect registered Docker runtimes') from None
        if setting not in available:raise ValueError('Selected Docker runtime is not registered')
    return setting


def check_elf(data, architecture):
    if (len(data) < 20 or data[:4] != b'\x7fELF' or data[4:6] != b'\x02\x01'
            or int.from_bytes(data[18:20], 'little') != ARCHITECTURES[architecture]['elf']):
        raise ValueError('Wrong ELF architecture, class or endianness')


def check_pe(data, architecture):
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('Invalid EFI PE header')
    offset = int.from_bytes(data[0x3c:0x40], 'little')
    if (offset + 6 > len(data) or data[offset:offset+4] != b'PE\0\0'
            or int.from_bytes(data[offset+4:offset+6], 'little') != ARCHITECTURES[architecture]['pe']):
        raise ValueError('Wrong EFI architecture')


def check_kernel(data, architecture):
    if architecture == 'arm64':
        valid = len(data) >= 64 and data[56:60] == b'ARMd'
    else:
        valid = (len(data) >= 0x238 and data[0x202:0x206] == b'HdrS'
                 and int.from_bytes(data[0x236:0x238], 'little') & 1)
    if not valid:
        raise ValueError('Wrong kernel architecture/header')


def preflight(architecture, root=Path('.'), minimum_disk_gib=160, minimum_memory_gib=8, require_kvm=False):
    import os
    import shutil
    native(architecture)
    if shutil.disk_usage(root).free < minimum_disk_gib * 1024**3:
        raise ValueError(f'Build needs at least {minimum_disk_gib} GiB free task storage')
    available = next(int(x.split()[1])*1024 for x in Path('/proc/meminfo').read_text().splitlines()
                     if x.startswith('MemAvailable:'))
    if available < minimum_memory_gib * 1024**3:
        raise ValueError(f'Build needs at least {minimum_memory_gib} GiB available memory')
    if require_kvm and not os.access('/dev/kvm', os.R_OK | os.W_OK):
        raise ValueError('Selected KVM mode requires a usable KVM device; no automatic accelerator fallback')
