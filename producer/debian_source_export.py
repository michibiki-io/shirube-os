"""Export exact applied Debian sources without unlicensed source-only tests."""
import json
from pathlib import Path
import shutil
from .debian_sources import descriptor
from .sources import sha256


def prepare(source, version, originals, tree, declarations):
    """Return provenance for a selected export; never distribute its raw tarball.

    This changes delivery sources only. Installed library code and headers are
    untouched. Original descriptor hashes still identify the binary's input;
    the separate export record describes the precise delivery modifications.
    """
    if declarations.get('schema_version') != 1:
        raise ValueError('Unknown Debian source export declaration schema')
    selected = [r for r in declarations['components'] if r['source'] == source]
    if not selected:
        return None
    if len(selected) != 1 or selected[0]['version'] != version:
        raise ValueError('Unreviewed Debian preferred-source export version')
    rule = selected[0]
    if source != 'libxcrypt' or version != '1:4.4.33-2':
        raise ValueError('Unsupported Debian preferred-source export')
    tree = Path(tree).resolve()
    dsc = descriptor(originals, source, version)
    if sha256(dsc) != rule['descriptor_sha256']:
        raise ValueError('Debian source export descriptor differs')

    def regular(member):
        parts = Path(member).parts
        if not parts or Path(member).is_absolute() or '..' in parts:
            raise ValueError('Unsafe source export member')
        path = tree
        for part in parts:
            path = path/part
            if path.is_symlink():
                raise ValueError('Aliased source export member')
        if not path.is_file() or not path.resolve().is_relative_to(tree):
            raise ValueError('Missing regular source export member')
        return path

    # Validate the entire bounded operation before changing any source file.
    expected = {'test/'+name for name in (
        'alg-des.c', 'alg-md4.c', 'alg-md5.c', 'alg-sha256.c', 'alg-sha512.c',
        'des-cases.h', 'des-obsolete.c', 'des-obsolete_r.c', 'gensalt.c')}
    rows = rule['excluded']
    if len(rows) != len(expected) or {r['member'] for r in rows} != expected:
        raise ValueError('Unexpected source-only test exclusions')
    if sha256(regular('LICENSING')) != rule['licensing_sha256']:
        raise ValueError('Original source-only test license declaration differs')
    for row in rows:
        if sha256(regular(row['member'])) != row['sha256']:
            raise ValueError('Excluded source-only test differs')
    control = regular('Makefile.am')
    if (rule['build_control']['path'] != 'Makefile.am' or
            sha256(control) != rule['build_control']['sha256']):
        raise ValueError('Original source export build control differs')
    content = control.read_bytes()
    line = b'\ttest/des-cases.h \\\n'
    if content.count(line) != 1:
        raise ValueError('Source-only header prerequisite differs')
    marker = tree/'SHIRUBE-SOURCE-EXPORT.json'
    original_descriptor = tree/'SHIRUBE-ORIGINAL-SOURCE.dsc'
    if any(p.exists() or p.is_symlink() for p in (marker, original_descriptor)):
        raise ValueError('Source export destination already exists')
    modified = (b'# Modified by michibiki-io 2026-11-10: omit unlicensed '
                b'source-only test header from all-am.\n'+content.replace(line, b''))
    for row in rows:
        regular(row['member']).unlink()
    control.write_bytes(modified)
    shutil.copyfile(dsc, original_descriptor)
    provenance = {
        'kind': 'debian-libxcrypt-preferred-source-without-unlicensed-tests',
        'source': source, 'version': version,
        'original_descriptor_sha256': sha256(dsc),
        'excluded': rows,
        'modified_build_control': {
            'member': 'Makefile.am', 'original_sha256': rule['build_control']['sha256'],
            'modified_sha256': sha256(control)},
        'modified_by': 'michibiki-io', 'modified_on': '2026-11-10',
        'reconstruction': (
            'This is the applied preferred source, not a new signed Debian source set. '
            'Original descriptor is retained as provenance only; raw original archives '
            'are not part of this export. All library/algorithm/header sources, Debian '
            'packaging and patches remain unchanged. Run autoreconf -fi, configure '
            'with the retained Debian options, make and make install in the native '
            'toolchain. For Debian binary packaging set DEB_BUILD_OPTIONS=nocheck '
            'and use dpkg-buildpackage -b. The omitted tests do not contribute to '
            'installed library code or headers. The original check suite is incomplete '
            'and must not be reported as passing. Seven other historical unlicensed '
            'test names in LICENSING are already absent from this original release.')}
    marker.write_text(json.dumps(provenance, indent=2)+'\n')
    return {'kind': provenance['kind'], 'record': 'source/SHIRUBE-SOURCE-EXPORT.json',
            'sha256': sha256(marker), 'original_descriptor_sha256': sha256(dsc)}
