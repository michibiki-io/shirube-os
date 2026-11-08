"""Build individually verifiable source archives from explicitly collected inputs."""
import gzip
import hashlib
from pathlib import Path
import tarfile
from .release import LIMIT, filename, scan_secrets
from .sources import sha256


def bundle(inputs, destination, epoch, source_origin=None):
    """inputs = [(regular_file, archive_member, category), ...].

    The caller must supply the exact source/license/recipe set for a shipped
    component. This function establishes byte integrity, not license clearance.
    Archives are component-sized; an oversized component stops publication.
    """
    destination = Path(destination)
    filename(destination.name)
    names = set()
    records = {'sources':[], 'license_files':[], 'build_inputs':[]}
    checked = []
    for path, member, category in inputs:
        path = Path(path)
        parts = Path(member)
        if (parts.is_absolute() or '..' in parts.parts or '.git' in parts.parts
                or not parts.parts or str(parts) != member or '\\' in member or member in names
                or category not in records or not path.is_file() or path.is_symlink()):
            raise ValueError('Unsafe, duplicate or missing source bundle input')
        names.add(member)
        digest=sha256(path)
        try:scan_secrets(path)
        except ValueError:
            from .source_fixtures import allowed
            if category!='sources' or not allowed(source_origin,member,digest):raise
        checked.append((path, member, category, digest))
    if not all(any(x[2] == category for x in checked) for category in records):
        raise ValueError('Source, license and reconstruction inputs are all mandatory')
    with destination.open('xb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
            with tarfile.open(mode='w|', fileobj=compressed) as archive:
                for path, member, category, expected in sorted(checked, key=lambda x:x[1]):
                    if sha256(path) != expected:
                        raise ValueError('Source input changed during collection')
                    header = tarfile.TarInfo(member)
                    header.size = path.stat().st_size
                    header.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                    header.mtime = epoch
                    with path.open('rb') as source:
                        archive.addfile(header, source)
                    records[category].append({'asset':destination.name, 'member':member, 'sha256':expected})
    if not 0 < destination.stat().st_size < LIMIT:
        raise ValueError('Source component exceeds the release per-asset limit; repartition before upload')
    # Verify archived bytes, rather than trusting successful archive creation.
    with tarfile.open(destination) as archive:
        actual = {}
        for member in archive:
            with archive.extractfile(member) as source:
                actual[member.name] = hashlib.file_digest(source, 'sha256').hexdigest()
    if actual != {member:digest for _,member,_,digest in checked}:
        raise ValueError('Source archive byte verification failed')
    return records
