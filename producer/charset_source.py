"""Export the fixed MIT library without uncleared, source-only sample texts."""
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

ORIGINAL = '3bb3d25a8e6c0aedd251753a79ae98a093c7e7b471faa3aa9a93a81431987ace'
ROOT = 'charset-normalizer-3.2.0'
KIND = 'charset-normalizer-3.2.0-without-uncleared-test-data'


def write_export(original, destination, declaration):
    """Preserve every retained file byte; this does not grant license clearance.

    The collector separately requires the exact resulting archive's pinned hash.
    Omitted samples cannot be used by the exported upstream pytest suite; they
    are excluded by the original package configuration from installed libraries.
    """
    if hashlib.sha256(Path(original).read_bytes()).hexdigest() != ORIGINAL:
        raise ValueError('Unexpected charset-normalizer source input')
    if declaration.get('kind') != KIND:
        raise ValueError('Unexpected charset-normalizer source export policy')
    omitted = declaration.get('excluded', [])
    expected = {item['member']: item['sha256'] for item in omitted}
    if len(expected) != len(omitted) or not expected:
        raise ValueError('Missing or duplicate excluded test sample declaration')
    for member, digest in expected.items():
        path = PurePosixPath(member)
        if (path.parent != PurePosixPath(ROOT+'/data') or
                not path.name.startswith('sample-') or path.suffix != '.txt' or
                len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)):
            raise ValueError('Only exact source-only charset test samples may be omitted')
    retained = {}; found = set()
    with tarfile.open(original) as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts or
                    path.parts[0] != ROOT or str(path) != member.name or '\\' in member.name):
                raise ValueError('Unsafe charset source member')
            if member.isdir():continue
            if not member.isfile() or member.name in retained or member.name in found:
                raise ValueError('Nonregular or duplicate charset source member')
            content = archive.extractfile(member).read()
            if member.name in expected:
                if hashlib.sha256(content).hexdigest() != expected[member.name]:
                    raise ValueError('Excluded source sample bytes differ')
                found.add(member.name);continue
            if str(path.parent) == ROOT+'/data' and path.name != 'NOTICE.md':
                raise ValueError('Unclassified charset test data remains')
            retained[member.name] = (content, 0o755 if member.mode & 0o111 else 0o644)
    if found != expected.keys():raise ValueError('Declared excluded source sample is missing')
    for member in ['LICENSE','data/NOTICE.md','setup.py','setup.cfg','charset_normalizer/__init__.py']:
        if ROOT+'/'+member not in retained:raise ValueError('Required library source or notice is missing')
    manifest = ROOT+'/SHIRUBE-SOURCE-EXPORT.json'
    if manifest in retained:raise ValueError('Unexpected source export notice')
    record = {'kind':KIND,'modified_by':'michibiki-io','modified_on':'2026-11-09',
              'original_source_sha256':ORIGINAL,'excluded':omitted,
              'scope':'Only source-distribution test samples with unresolved per-file attribution are omitted. All library/build code and original notices are unchanged. The upstream sample-based pytest suite is unavailable; runtime and build functionality are retained.'}
    retained[manifest] = ((json.dumps(record,sort_keys=True,indent=2)+'\n').encode(),0o644)
    with Path(destination).open('xb') as raw:
        with gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as compressed:
            with tarfile.open(fileobj=compressed,mode='w|') as output:
                for name,(content,mode) in sorted(retained.items()):
                    member=tarfile.TarInfo(name);member.mode=mode;member.mtime=1773859634;member.size=len(content)
                    output.addfile(member,io.BytesIO(content))
    return hashlib.sha256(Path(destination).read_bytes()).hexdigest()
