"""Batch Debian source components without changing any original source bytes."""
import hashlib
from pathlib import Path
import tarfile

from .release import LIMIT, filename
from .sources import sha256


def batch(archives, components, output, architecture, version, *, max_components=16,
          max_bytes=1024**3):
    """Return replacement assets after independently checking every new member.

    Only explicitly selected Debian archives are eligible. Custom source formats,
    VPP provenance and public-test-key allowances keep their original namespaces.
    Parts are plain tar: their already-compressed original Debian input archives
    do not need another compression pass. Each component has its own directory.
    """
    if max_components < 2 or not 0 < max_bytes < LIMIT:
        raise ValueError('Invalid Debian source batch budget')
    originals = {filename(Path(p).name): Path(p) for p in archives}
    if len(originals) != len(archives):
        raise ValueError('Duplicate Debian source archive')
    expected = {name: {} for name in originals}
    references = []

    def visit(rows):
        for component in rows:
            for category in ['sources', 'license_files', 'build_inputs']:
                for reference in component.get(category, []):
                    name = reference['asset']
                    if name not in originals:
                        continue
                    member = reference['member']; digest = reference['sha256']
                    prior = expected[name].setdefault(member, digest)
                    if prior != digest:
                        raise ValueError('Conflicting Debian source reference')
                    references.append(reference)
            visit(component.get('bundled_components', []))
    visit(components)
    identities = {}; sizes = {}
    for name, path in originals.items():
        if path.is_symlink() or not path.is_file() or not expected[name]:
            raise ValueError('Missing exact Debian source archive or references')
        identities[name] = sha256(path)
        observed = set(); size = 10240
        with tarfile.open(path) as source:
            for member in source:
                if member.isdir():
                    continue
                relative = Path(member.name)
                if (not member.isfile() or relative.is_absolute() or '..' in relative.parts
                        or '.git' in relative.parts or str(relative) != member.name
                        or '\\' in member.name or member.name in observed
                        or member.name not in expected[name]):
                    raise ValueError('Unsafe or unreferenced Debian source member')
                observed.add(member.name)
                # Includes conservative room for tar/PAX headers and padding.
                size += ((member.size+511)//512)*512+4096
        if observed != expected[name].keys():
            raise ValueError('Incomplete Debian source archive')
        sizes[name] = size
    groups = []; pending = []; occupied = 0
    for name in sorted(originals):
        if pending and (len(pending) >= max_components or occupied+sizes[name] > max_bytes):
            groups.append(pending); pending = []; occupied = 0
        pending.append(name); occupied += sizes[name]
    if pending:
        groups.append(pending)
    created = []; remap = {}
    try:
        for group in groups:
            if len(group) < 2:
                continue
            target = Path(output)/filename(
                f'shirube-os-{version}-{architecture}-debian-sources-{len(created)+1:04d}.tar')
            # Reserve a new owned file before including it in failure cleanup.
            with target.open('xb') as raw:
                created.append(target)
                wanted = {}
                with tarfile.open(fileobj=raw, mode='w') as destination:
                    for name in group:
                        original = originals[name]
                        if sha256(original) != identities[name]:
                            raise ValueError('Debian source input changed before batching')
                        prefix = 'components/'+name.removesuffix('.tar.gz')+'/'
                        with tarfile.open(original) as source:
                            for member in source:
                                if member.isdir():
                                    continue
                                renamed = prefix+member.name
                                header = tarfile.TarInfo(renamed)
                                header.size = member.size
                                header.mode = 0o755 if member.mode & 0o111 else 0o644
                                header.mtime = member.mtime
                                with source.extractfile(member) as stream:
                                    destination.addfile(header, stream)
                                wanted[renamed] = expected[name][member.name]
                                remap[(name, member.name)] = (target.name, renamed)
                        if sha256(original) != identities[name]:
                            raise ValueError('Debian source input changed during batching')
            if not 0 < target.stat().st_size < LIMIT:
                raise ValueError('Debian source part exceeds the release asset limit')
            actual = {}
            with tarfile.open(target) as archive:
                for member in archive:
                    if not member.isfile() or member.name in actual:
                        raise ValueError('Invalid Debian source batch member')
                    with archive.extractfile(member) as stream:
                        actual[member.name] = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != wanted:
                raise ValueError('Batched Debian source bytes differ from exact component references')
        # Originals and inventory remain intact until all parts have passed.
    except BaseException:
        for path in created:
            path.unlink()
        raise
    for reference in references:
        replacement = remap.get((reference['asset'], reference['member']))
        if replacement is not None:
            reference['asset'], reference['member'] = replacement
    replaced = {name for name, _ in remap}
    for name in replaced:
        originals[name].unlink()
    return created, replaced
