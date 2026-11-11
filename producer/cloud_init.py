"""Build the pinned public VyOS cloud-init fork with secure enrollment defaults."""
import ast
import json
from pathlib import Path
import shutil
import subprocess

from .sources import download, extract, sha256


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Pinned cloud-init source contract changed')
    return text.replace(before, after)


def redact_diagnostics(text):
    """Keep diagnostics, but never interpolate user-data/configuration values."""
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    changes = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == 'logger' and node.args):
            argument = node.args[0]
            if isinstance(argument, ast.Call) and isinstance(argument.func, ast.Attribute) and argument.func.attr == 'format':
                label = argument.func.value
                if not isinstance(label, ast.Constant) or not isinstance(label.value, str):
                    raise ValueError('Unexpected upstream cloud-init log format')
                # Fixed labels retain the phase; no user-controlled value or
                # exception text can enter the resulting message.
                import re
                message = re.sub(r'\{[^}]*\}', '[redacted]', label.value)
                changes.append((node.lineno, node.end_lineno, node.col_offset,
                                node.end_col_offset, 'logger.'+node.func.attr+'('+repr(message)+')'))
    for start, end, col, last, replacement in sorted(changes, reverse=True):
        lines[start-1:end] = [lines[start-1][:col]+replacement+lines[end-1][last:]]
    return ''.join(lines)


def patch_source(source):
    source = Path(source)
    path = source/'cloudinit/config/cc_vyos.py'
    text = path.read_text()
    text = replace_once(text, "    DEFAULT_VYOS_USER = 'vyos'\n    DEFAULT_VYOS_PASSWORD = 'vyos'\n", '')
    text = replace_once(text, '''    # Create a fallback user if there was no others
    if not logins_configured:
        logger.debug("Adding fallback user: {}".format(DEFAULT_VYOS_USER))
        set_pass_login(config, DEFAULT_VYOS_USER, DEFAULT_VYOS_PASSWORD)
''', '''    # No shared fallback credential. Console enrollment handles absent auth.
    if not logins_configured:
        logger.info("No cloud administrator authentication supplied")
''')
    text = replace_once(text, "set_config_hostname(config, 'vyos', None)", "set_config_hostname(config, 'shirube-os', None)")
    text = replace_once(text, '    dsname = cloud.datasource.dsname\n',
        '    dsname = cloud.datasource.dsname\n'
        "    if dsname == 'None':\n"
        '        logger.info("No metadata datasource; preserving saved native configuration")\n'
        '        return\n')
    text = replace_once(text, "netcfg = netcfg.get('network', netcfg)", "netcfg = (netcfg or {}).get('network', netcfg or {})")
    text = replace_once(text, 'import re\n', 'import re\nfrom passlib.hash import sha512_crypt\n')
    text = replace_once(text,
        "config.set(['system', 'login', 'user', user, 'authentication', 'plaintext-password'], value=password, replace=True)",
        "config.set(['system', 'login', 'user', user, 'authentication', 'encrypted-password'], value=sha512_crypt.hash(password), replace=True)")
    text = replace_once(text, "metadata_v1 = instance_data_json.get('v1')", "metadata_v1 = instance_data_json.get('v1') or {}")
    text = replace_once(text, "ssh_keys = [key for key in metadata_v1['public_ssh_keys'] if key]",
                        "ssh_keys = [key for key in metadata_v1.get('public_ssh_keys', []) if key]")
    path.write_text(redact_diagnostics(text))
    path = source/'cloudinit/config/cc_vyos_userdata.py'
    path.write_text(redact_diagnostics(path.read_text()))
    path = source/'config/cloud.cfg.d/10_vyos.cfg'
    text = replace_once(path.read_text(), '    name: vyos', '    name: shirube')
    path.write_text(text)
    for relative in ['cloudinit/config/cc_vyos.py', 'cloudinit/config/cc_vyos_userdata.py',
                     'config/cloud.cfg.d/10_vyos.cfg']:
        path = source/relative
        path.write_text('# Modified by michibiki-io for Shirube OS on 2026-11-11.\n'+path.read_text())


def copyright_notice(original):
    original=Path(original)
    notice=(original/'packages/debian/copyright').read_bytes()
    notice+=b'\nVyOS configuration modules retain individual GPL-3.0-only or GPL-2.0-or-later notices; retain original headers.\n'
    for module in sorted((original/'cloudinit/config').glob('*vyos*.py')):
        header=[]
        for line in module.read_text().splitlines():
            if line and not line.startswith('#'):break
            header.append(line)
        notice+=('\n'+module.name+'\n'+'\n'.join(header)+'\n').encode()
    for name in ['LICENSE','LICENSE-GPLv3','LICENSE-Apache2.0']:
        notice+=b'\n'+(original/name).read_bytes()
    return notice


def build(project, work, debs, source_archive=None):
    project, work, debs = map(Path, [project, work, debs])
    work.mkdir()
    debs.mkdir(parents=True, exist_ok=True)
    lock = json.loads((project/'source-lock.json').read_text())['cloud_init']
    archive = work/'upstream.tar.gz'
    if source_archive is None:
        download(lock['url'], archive)
    else:
        if Path(source_archive).is_symlink() or sha256(source_archive)!=lock['sha256']:
            raise ValueError('Restored cloud-init source checksum mismatch')
        shutil.copy2(source_archive, archive)
    if sha256(archive) != lock['sha256']:
        raise ValueError('Cloud-init source checksum mismatch')
    extract(archive, work/'original')
    original = work/'original'/('vyos-cloud-init-'+lock['commit'])
    source = work/'source'
    shutil.copytree(original, source)
    patch_source(source)
    package = work/'package'
    subprocess.run(['python3', 'setup.py', 'install', '--root='+str(package),
                    '--prefix=/usr', '--install-lib=/usr/lib/python3/dist-packages',
                    '--install-scripts=/usr/bin',
                    '--init-system=systemd', '--distro', 'debian'], cwd=source, check=True)
    doc = package/'usr/share/doc/cloud-init'
    doc.mkdir(parents=True, exist_ok=True)
    for name in ['LICENSE', 'LICENSE-GPLv3', 'LICENSE-Apache2.0']:
        shutil.copy2(original/name, doc/name)
    (doc/'copyright').write_bytes(copyright_notice(original))
    wants = package/'etc/systemd/system/cloud-init.target.wants'
    wants.mkdir(parents=True, exist_ok=True)
    for unit in ['cloud-init-local.service', 'cloud-init.service', 'cloud-config.service', 'cloud-final.service']:
        (wants/unit).symlink_to('/lib/systemd/system/'+unit)
    control = package/'DEBIAN'
    control.mkdir()
    (control/'control').write_text('Package: cloud-init\nVersion: 22.1+shirube1\nArchitecture: all\n'
        'Maintainer: michibiki-io <maintainers@michibiki.io>\n'
        'Depends: python3, python3-passlib, python3-jinja2, python3-oauthlib, python3-configobj, python3-yaml, '
        'python3-requests, python3-jsonpatch, python3-jsonschema, python3-netifaces, iproute2, isc-dhcp-client, ifupdown, cloud-guest-utils, vyos-1x\n'
        'Description: Upstream-native cloud initialization for Shirube OS\n')
    target = debs/'cloud-init_22.1+shirube1_all.deb'
    subprocess.run(['dpkg-deb', '--root-owner-group', '-b', str(package), str(target)], check=True)
    return target
