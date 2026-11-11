"""Retain the fixed libxcrypt library without unlabelled source-only tests."""
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

ORIGINAL = '71513a31c01a428bccd5367a32fd95f115d6dac50fb5b60c779d5c7942aec071'
ROOT = 'libxcrypt-4.5.2'
KIND = 'libxcrypt-4.5.2-without-unlabelled-source-only-tests'
TESTS = frozenset(ROOT + '/test/' + name for name in (
    'alg-des.c', 'alg-md4.c', 'alg-md5.c', 'alg-sha256.c', 'alg-sha512.c',
    'des-cases.h', 'des-obsolete.c', 'des-obsolete_r.c', 'gensalt.c'))
SCOPE = ('Only nine original source-only test files identified by the upstream LICENSING '
         'inventory as having unknown individual copyrights/licenses are omitted. All '
         'library, algorithm, public header, configure/install code and original legal '
         'files are preserved byte-for-byte. Only the two original Makefile control '
         'files remove the now-absent test header from the noninstalled all-am '
         'prerequisite list; original attribution remains intact. The upstream check '
         'suite is incomplete and must not be reported as passing. Normal library '
         'build/install is verified separately.')


def write_export(original, destination, declaration):
    if hashlib.sha256(Path(original).read_bytes()).hexdigest() != ORIGINAL:
        raise ValueError('Unexpected libxcrypt source input')
    omitted = declaration.get('excluded', [])
    expected = {item['member']: item['sha256'] for item in omitted}
    if (declaration.get('kind') != KIND or set(expected) != TESTS or
            len(expected) != len(omitted)):
        raise ValueError('Only the exact unlabelled source-only libxcrypt tests may be omitted')
    retained = {}; found = set()
    with tarfile.open(original) as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts or
                    path.parts[0] != ROOT or str(path) != member.name or '\\' in member.name):
                raise ValueError('Unsafe libxcrypt source member')
            if member.isdir(): continue
            if not member.isfile() or member.name in retained or member.name in found:
                raise ValueError('Nonregular or duplicate libxcrypt source member')
            content = archive.extractfile(member).read()
            if member.name in expected:
                if hashlib.sha256(content).hexdigest() != expected[member.name]:
                    raise ValueError('Excluded libxcrypt test bytes differ')
                found.add(member.name); continue
            retained[member.name] = (content, 0o755 if member.mode & 0o111 else 0o644)
    if found != TESTS:
        raise ValueError('Declared libxcrypt source-only test is missing')
    modifications = []
    for name in ('Makefile.am', 'Makefile.in'):
        member = ROOT + '/' + name
        original_control, mode = retained[member]
        line = b'\ttest/des-cases.h \\\n'
        if original_control.count(line) != 1:
            raise ValueError('Original libxcrypt build control differs')
        modified = (b'# Modified by michibiki-io 2026-11-10: omit unlicensed source-only '
                    b'test header from all-am.\n' + original_control.replace(line, b''))
        retained[member] = (modified, mode)
        modifications.append({'member': member,
                              'original_sha256': hashlib.sha256(original_control).hexdigest(),
                              'modified_sha256': hashlib.sha256(modified).hexdigest()})
    for name in ('LICENSING', 'COPYING.LIB', 'AUTHORS', 'configure', 'lib/crypt.c'):
        if ROOT + '/' + name not in retained:
            raise ValueError('Required library source, build control or notice is missing')
    manifest = ROOT + '/SHIRUBE-SOURCE-EXPORT.json'
    if manifest in retained:
        raise ValueError('Unexpected libxcrypt source export notice')
    record = {'kind': KIND, 'modified_by': 'michibiki-io', 'modified_on': '2026-11-10',
              'original_source_sha256': ORIGINAL, 'excluded': omitted,
              'modified_build_controls': modifications, 'scope': SCOPE}
    retained[manifest] = ((json.dumps(record, sort_keys=True, indent=2)+'\n').encode(), 0o644)
    with Path(destination).open('xb') as raw:
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w|') as output:
                for name, (content, mode) in sorted(retained.items()):
                    member = tarfile.TarInfo(name); member.mode = mode
                    member.mtime = 1773859634; member.size = len(content)
                    output.addfile(member, io.BytesIO(content))
    return hashlib.sha256(Path(destination).read_bytes()).hexdigest()
