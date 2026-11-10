"""Keep GNU command launchers separate from Nmap-owned result presentation."""
from pathlib import Path
from .sanitize import path_in_root
from .sources import sha256

ORIGINAL = '687bfe57626a2a514f383b79bff493a81c7da6ecb46aeab4b6901f2ddec4fffa'
DELIVERED = 'dfaceade48570e622b44b0ec5017156ff0df7041c84f8c705d40d0042c17cdd5'
PORT_SCAN = '5297b7c7855e253573ae3b5b1ec9ad7156884b1a4722e6353ac82a7049a71a4f'
SCRIPT_ORIGINAL = 'ccee060d4ca79b2a1a96b83a034de4fad9759b863da3a515fc8f1e7ae0c1d645'
SCRIPT = '36a4c9b068ae74b44cecb47f3d5328c3e4491d2f860dd4d2954be6d4ed401c23'
NPSL = '4e28b9b2630e119657f0deb0eee8bb7d33d3edb9e2a59c24e238e739565f1bef'
SCRIPT_MEMBER = 'usr/share/nmap/scripts/shirube-path-mtu.nse'
NOTICE_MEMBER = 'usr/share/doc/nmap/NPSL-0.94.txt'


def prepare(sources, replacement):
    root=Path(sources).resolve()
    if not (root/'vyos-1x').exists() and not (root/'vyos-1x').is_symlink():
        return None
    source=root/'vyos-1x/vyos-1x/src/op_mode/force_mtu_host.sh'
    replacement=Path(replacement)
    if replacement.is_symlink() or sha256(replacement)!=DELIVERED:
        raise ValueError('Unreviewed path-MTU launcher')
    if (source.is_symlink() or not source.is_file() or
            not source.resolve().is_relative_to(root) or sha256(source) not in {ORIGINAL,DELIVERED}):
        raise ValueError('Original upstream path-MTU helper differs')
    source.write_bytes(replacement.read_bytes());source.chmod(0o755)
    return {'kind':'nmap-native-pmtu-presentation','original_launcher_sha256':ORIGINAL,
            'delivered_launcher_sha256':DELIVERED,'launcher_license':'GPL-2.0-only',
            'nmap_script_sha256':SCRIPT,'script_license':'LicenseRef-Nmap-NPSL-0.94',
            'modified_by':'michibiki-io','modified_on':'2026-11-10'}


def install_script(core, script, notice):
    inputs=[(Path(script),SCRIPT_MEMBER,SCRIPT),(Path(notice),NOTICE_MEMBER,NPSL)]
    for source,member,expected in inputs:
        if source.is_symlink() or not source.is_file() or sha256(source)!=expected:
            raise ValueError('Unreviewed Nmap script or full license')
        target=path_in_root(core,member)
        if target.exists() and (not target.is_file() or sha256(target)!=expected):
            raise ValueError('Nmap script installation would replace unrelated content')
    for source,member,_ in inputs:
        target=path_in_root(core,member);target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(source.read_bytes());target.chmod(0o644)


def verify(root):
    # GNU launchers construct arguments and pass scanner output to the console.
    # The original probe and result formatting run in Nmap's NPSL script.
    for member,expected in [
        ('usr/libexec/vyos/op_mode/force_mtu_host.sh',DELIVERED),
        ('usr/libexec/vyos/op_mode/execute_port-scan.py',PORT_SCAN),
        (SCRIPT_MEMBER,SCRIPT),(NOTICE_MEMBER,NPSL)]:
        path=path_in_root(root,member)
        if not path.is_file() or sha256(path)!=expected:
            raise ValueError('Unreviewed Nmap diagnostic integration')


def retained_script(root):
    verify(root)
    return path_in_root(root,SCRIPT_MEMBER),path_in_root(root,NOTICE_MEMBER)
