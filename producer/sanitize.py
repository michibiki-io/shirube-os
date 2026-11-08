"""Offline root sanitization; invoke only on a stopped disposable fresh install."""
from pathlib import Path
import shutil

HOSTS = ('127.0.0.1 localhost\n127.0.1.1 shirube-os\n'
         '::1 localhost ip6-localhost ip6-loopback\n'
         'ff02::1 ip6-allnodes\nff02::2 ip6-allrouters\n')

REMOVE = ('config/auth', 'config/netbird', 'config/archive', 'config/user-data',
          'config/config.boot', 'config/config.boot.default', 'config/.history',
          'config/shirube-enrolled',
          'root/.ssh', 'root/.bash_history', 'home', 'var/log', 'tmp', 'var/tmp', 'var/lib/cloud',
          'var/lib/netbird', 'var/lib/dhcp', 'var/lib/NetworkManager',
          'var/lib/systemd/random-seed', 'var/lib/dbus/machine-id',
          'etc/udev/rules.d/70-persistent-net.rules', 'etc/ssl/private',
          'etc/shadow-', 'etc/gshadow-', 'etc/passwd-', 'etc/group-')


def path_in_root(root, relative):
    """Resolve symlinks using guest-root semantics, never host absolute paths."""
    root = Path(root).resolve()
    if root == Path('/') or not (root / 'etc').is_dir():
        raise ValueError('A mounted guest root is required')
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Unsafe guest path')
    pending = list(relative.parts)
    resolved = []
    links = 0
    while pending:
        part = pending.pop(0)
        if part in ('', '.'):
            continue
        if part == '..':
            if not resolved:
                raise ValueError('Guest link escapes root')
            resolved.pop(); continue
        target = root.joinpath(*resolved, part)
        if target.is_symlink():
            links += 1
            if links > 40:
                raise ValueError('Cyclic guest symlinks')
            link = Path(target.readlink())
            if link.is_absolute():
                resolved = []
                pending = list(link.parts[1:]) + pending
            else:
                pending = list(link.parts) + pending
        else:
            resolved.append(part)
    return root.joinpath(*resolved)


def erase(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)



CONFIG_ROOTS = ('config', 'opt/vyatta/etc/config')


def factory_config(architecture):
    console = 'ttyAMA0' if architecture == 'arm64' else 'ttyS0'
    return 'interfaces {\n ethernet eth0 {\n  address dhcp\n }\n loopback lo {\n }\n}\nsystem {\n host-name shirube-os\n console {\n  device '+console+' {\n   speed 115200\n  }\n }\n}\n'



def host_key_paths(root):
    for parent, pattern in [('etc/ssh','ssh_host_*'),('etc/dropbear','*host_key*'),
                            ('etc/dropbear-initramfs','*host_key*')]:
        for path in path_in_root(root,parent).rglob(pattern):
            if path.is_file() or path.is_symlink():
                yield path


def sanitize(root, architecture):
    if architecture not in {'amd64', 'arm64'}:
        raise ValueError('Unsupported architecture')
    root = Path(root).resolve()
    if path_in_root(root, 'etc/shadow').is_symlink() or not path_in_root(root, 'etc/shadow').is_file():
        raise ValueError('Guest account database is missing')
    for relative in REMOVE:
        erase(path_in_root(root, relative))
    # /config is a runtime bind mount of the native persistent directory.
    # A non-recursive offline root view contains both underlying directories.
    configurations={path_in_root(root,relative) for relative in CONFIG_ROOTS}
    for configuration in configurations:
        configuration.mkdir(parents=True,exist_ok=True)
        for entry in configuration.iterdir():
            erase(entry)
    for path in list(host_key_paths(root)):
        relative=path.relative_to(root)
        erase(path_in_root(root,relative.parent)/relative.name)
    for pattern in ('config/*history*', 'root/.*history'):
        for path in root.glob(pattern):
            erase(path_in_root(root, str(path.relative_to(root))))
    # Lock every installed account, including installer/default users.
    passwd=path_in_root(root,'etc/passwd')
    removed=set()
    if passwd.is_file():
        records=[]
        for record in passwd.read_text().splitlines():
            fields=record.split(':')
            if len(fields)!=7:raise ValueError('Unexpected guest account record')
            if 1000 <= int(fields[2]) < 65534 and fields[0]!='vyos':
                removed.add(fields[0])
            else:records.append(record)
        passwd.write_text('\n'.join(records)+'\n')
    for database in ['etc/group','etc/gshadow']:
        file=path_in_root(root,database)
        if not file.is_file():continue
        records=[]
        for record in file.read_text().splitlines():
            fields=record.split(':')
            if len(fields)!=4:raise ValueError('Unexpected guest group record')
            if fields[0] in removed:continue
            fields[3]=','.join(x for x in fields[3].split(',') if x and x not in removed)
            if database.endswith('gshadow'):
                fields[1]='!';fields[2]=','.join(x for x in fields[2].split(',') if x and x not in removed)
            records.append(':'.join(fields))
        file.write_text('\n'.join(records)+'\n')
    shadow = path_in_root(root, 'etc/shadow')
    rows = []
    for line in shadow.read_text().splitlines():
        fields = line.split(':')
        if len(fields) != 9:
            raise ValueError('Unexpected guest shadow record')
        if fields[0] in removed:continue
        fields[1] = '!'
        rows.append(':'.join(fields))
    shadow.write_text('\n'.join(rows)+'\n')
    shadow.chmod(0o600)
    identity = path_in_root(root, 'etc/machine-id')
    erase(identity)
    identity.write_text('')
    path_in_root(root,'etc/hostname').write_text('shirube-os\n')
    path_in_root(root,'etc/hosts').write_text(HOSTS)
    for relative in ('var/log', 'home', 'config'):
        path_in_root(root, relative).mkdir(parents=True, exist_ok=True)
    for relative in ('tmp','var/tmp'):
        path_in_root(root,relative).mkdir(parents=True,exist_ok=True)
        path_in_root(root,relative).chmod(0o1777)
    # Each native/offline configuration view receives the same credential-free
    # defaults; no enrollment completion, peer identity or saved user remains.
    for configuration in configurations:
        (configuration/'config.boot').write_text(factory_config(architecture))
        marker=configuration/'shirube-first-boot'
        marker.write_text('pending\n');marker.chmod(0o600)
    return audit(root)


def audit(root):
    root = Path(root).resolve()
    for relative in REMOVE:
        if relative == 'config/config.boot':
            continue
        if relative in ('home', 'var/log', 'tmp', 'var/tmp') and not any(path_in_root(root, relative).iterdir()):
            continue
        path = path_in_root(root, relative)
        if path.exists() or path.is_symlink():
            raise ValueError('Forbidden state remains in export: '+relative)
    for relative in CONFIG_ROOTS:
        configuration=path_in_root(root,relative)
        if {entry.name for entry in configuration.iterdir()}!={'config.boot','shirube-first-boot'}:
            raise ValueError('Instance state remains in a native configuration directory: '+relative)
        if (configuration/'config.boot').read_text() not in {factory_config('amd64'),factory_config('arm64')}:
            raise ValueError('Export configuration differs from credential-free factory defaults')
    if (path_in_root(root,'etc/hostname').read_text()!='shirube-os\n' or
            path_in_root(root,'etc/hosts').read_text()!=HOSTS):
        raise ValueError('Build-instance hostname or hosts data remains')
    if path_in_root(root, 'etc/machine-id').read_bytes():
        raise ValueError('Fixed machine identity remains')
    if list(host_key_paths(root)):
        raise ValueError('SSH host identity remains')
    for line in path_in_root(root, 'etc/shadow').read_text().splitlines():
        if line.split(':')[1] not in ('!', '*', '!!'):
            raise ValueError('Reusable guest password credential remains')
    return {'logical_filesystem_cleanup': 'PASS', 'export_sanitization': 'NOT_RUN', 'boot_validation': 'NOT_RUN'}
