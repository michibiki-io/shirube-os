"""Offline root sanitization; invoke only on a stopped disposable fresh install."""
from pathlib import Path
import shutil

REMOVE = ('config/auth', 'config/netbird', 'config/archive', 'config/user-data',
          'config/config.boot', 'config/config.boot.default', 'config/.history',
          'root/.ssh', 'root/.bash_history', 'home', 'var/log', 'var/lib/cloud',
          'var/lib/netbird', 'var/lib/dhcp', 'var/lib/NetworkManager',
          'var/lib/systemd/random-seed', 'var/lib/dbus/machine-id',
          'etc/udev/rules.d/70-persistent-net.rules')


def path_in_root(root, relative):
    root = Path(root).resolve()
    if root == Path('/') or not (root / 'etc').is_dir():
        raise ValueError('A mounted offline guest root is required')
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Unsafe guest path')
    target = root / relative
    for parent in target.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError('Unexpected symlink in offline guest path')
    return target


def erase(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def sanitize(root, architecture):
    if architecture not in {'amd64', 'arm64'}:
        raise ValueError('Unsupported architecture')
    root = Path(root).resolve()
    if path_in_root(root, 'etc/shadow').is_symlink() or not path_in_root(root, 'etc/shadow').is_file():
        raise ValueError('Guest account database is missing')
    for relative in REMOVE:
        erase(path_in_root(root, relative))
    configuration = path_in_root(root, 'config')
    if configuration.is_symlink():
        raise ValueError('Mount the writable merged guest root with a physical config directory')
    for entry in configuration.iterdir():
        erase(entry)
    for pattern in ('etc/ssh/ssh_host_*', 'config/*history*', 'root/.*history'):
        for path in root.glob(pattern):
            erase(path_in_root(root, str(path.relative_to(root))))
    # Lock every installed account, including installer/default users.
    shadow = path_in_root(root, 'etc/shadow')
    rows = []
    for line in shadow.read_text().splitlines():
        fields = line.split(':')
        if len(fields) != 9:
            raise ValueError('Unexpected guest shadow record')
        fields[1] = '!'
        rows.append(':'.join(fields))
    shadow.write_text('\n'.join(rows)+'\n')
    shadow.chmod(0o600)
    identity = path_in_root(root, 'etc/machine-id')
    erase(identity)
    identity.write_text('')
    for relative in ('var/log', 'home', 'config'):
        path_in_root(root, relative).mkdir(parents=True, exist_ok=True)
    # Persistent settings contain no account, MAC, address or peer identity.
    console = 'ttyAMA0' if architecture == 'arm64' else 'ttyS0'
    path_in_root(root, 'config/config.boot').write_text('interfaces {\n ethernet eth0 {\n  address dhcp\n }\n loopback lo {\n }\n}\nsystem {\n host-name shirube-os\n console {\n  device '+console+' {\n   speed 115200\n  }\n }\n}\n')
    marker = path_in_root(root, 'config/shirube-first-boot')
    marker.write_text('pending\n')
    marker.chmod(0o600)
    return audit(root)


def audit(root):
    root = Path(root).resolve()
    for relative in REMOVE:
        if relative == 'config/config.boot':
            continue
        if relative in ('home', 'var/log') and not any(path_in_root(root, relative).iterdir()):
            continue
        path = path_in_root(root, relative)
        if path.exists() or path.is_symlink():
            raise ValueError('Forbidden state remains in export')
    if path_in_root(root, 'etc/machine-id').read_bytes():
        raise ValueError('Fixed machine identity remains')
    if list((root / 'etc/ssh').glob('ssh_host_*')):
        raise ValueError('SSH host identity remains')
    for line in path_in_root(root, 'etc/shadow').read_text().splitlines():
        if line.split(':')[1] not in ('!', '*', '!!'):
            raise ValueError('Reusable guest password credential remains')
    return {'logical_filesystem_cleanup': 'PASS', 'export_sanitization': 'NOT_RUN', 'boot_validation': 'NOT_RUN'}
