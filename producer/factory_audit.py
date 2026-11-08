"""Inspect factory root and initramfs contents without executing guest payloads."""
from pathlib import Path, PurePosixPath
import subprocess
import re
from .sources import sha256
from .sanitize import path_in_root, host_key_paths, HOSTS
from .release import SECRET
from .public_test_keys import allowed, allowed_file, cached_fixture_keys, scan


def files(root):
    for path in Path(root).rglob('*'):
        if not path.is_symlink() and path.is_file():
            yield path


def inspect_filesystems(root, initramfs, architecture):
    if architecture not in {'amd64', 'arm64'}:
        raise ValueError('Unsupported factory architecture')
    root, initramfs = Path(root), Path(initramfs)
    if not any((initramfs/member).is_file() for member in ['init','main/init']):
        raise ValueError('Unpacked initramfs entry point is missing')
    owners = {}
    for listing in (root/'var/lib/dpkg/info').glob('*.list'):
        package = listing.name.removesuffix('.list').split(':')[0]
        for line in listing.read_text().splitlines():
            owners[line.removeprefix('/')] = package
    if path_in_root(root, 'etc/machine-id').read_bytes():
        raise ValueError('Factory machine identity is not empty')
    if (path_in_root(root,'etc/hostname').read_text()!='shirube-os\n' or
            path_in_root(root,'etc/hosts').read_text()!=HOSTS):
        raise ValueError('Factory contains unexpected hostname or hosts defaults')
    shadow = path_in_root(root, 'etc/shadow').read_text().splitlines()
    if not shadow or any(len(row.split(':')) != 9 or row.split(':')[1] not in {'!', '*', '!!'} for row in shadow):
        raise ValueError('Factory has a reusable password credential')
    if not path_in_root(root, 'usr/share/shirube/needs-enrollment').is_file():
        raise ValueError('Factory enrollment marker is missing')
    if list(host_key_paths(root)):
        raise ValueError('Factory contains a host key file')
    for member in ['config/shirube-enrolled','opt/vyatta/etc/config/shirube-enrolled']:
        if path_in_root(root,member).exists() or (root/member).is_symlink():
            raise ValueError('Factory contains completed instance enrollment state')
    counts = {'root_files': 0, 'public_fixture_files': 0, 'initramfs_files': 0}
    for path in files(root):
        member = str(path.relative_to(root))
        package = owners.get(member, '')
        if path.parent.name == '__pycache__' and path.name.endswith('.cpython-311.pyc'):
            original = str(PurePosixPath(member).parent.parent /
                           (path.name.removesuffix('.cpython-311.pyc')+'.py'))
            package = owners.get(original, package)
        fixture = allowed_file(package, member)
        if fixture is not None and sha256(path) == fixture:
            counts['public_fixture_files'] += 1
        else:
            permits = allowed(package, member, architecture) | cached_fixture_keys(root, member, package)
            with path.open('rb') as stream:
                scan(stream, SECRET, permits)
        counts['root_files'] += 1
    for path in files(initramfs):
        member = str(path.relative_to(initramfs))
        # Debian may prepend a microcode archive; unmkinitramfs then places the
        # compressed filesystem below main/. Only byte-identical root payloads
        # can borrow that package's narrowly scoped public self-test allowances.
        if re.search(r'(^|/)etc/(ssh/ssh_host_|dropbear(?:-initramfs)?/.*host_key)',member):
            raise ValueError('Initramfs contains a host key file')
        relative = member.removeprefix('main/')
        permits = set()
        if owners.get(relative) == 'libgnutls30':
            original = path_in_root(root, relative)
            if original.is_file() and sha256(path) == sha256(original):
                permits = allowed('libgnutls30', relative, architecture)
        with path.open('rb') as stream:
            scan(stream, SECRET, permits)
        counts['initramfs_files'] += 1
    return {'status': 'PASS', 'scope': 'factory root and unpacked initramfs',
            'machine_id_empty': True, 'password_accounts_locked': True,
            'host_key_files_absent': True, 'pem_openssh_scan': 'PASS', 'counts': counts}


def audit_iso(iso, root, architecture, work):
    work = Path(work)
    work.mkdir()
    initrd = work/'initrd.img'
    subprocess.run(['xorriso', '-osirrox', 'on', '-indev', str(iso), '-extract',
                    '/live/initrd.img', str(initrd)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    unpacked = work/'initramfs'
    subprocess.run(['unmkinitramfs', str(initrd), str(unpacked)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {**inspect_filesystems(root, unpacked, architecture), 'iso_sha256': sha256(iso)}
