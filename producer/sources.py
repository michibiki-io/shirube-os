"""Pinned public acquisition and safe extraction, run inside a task container."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import urllib.request
from urllib.parse import urlsplit


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(url, target):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Only public HTTPS inputs without embedded credentials are supported')
    target = Path(target)
    temporary = target.with_name(target.name + '.partial')
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'shirube-os/0.1'})
        with urllib.request.urlopen(request, timeout=60) as source, temporary.open('xb') as output:
            if urlsplit(source.url).scheme != 'https':
                raise ValueError('Input redirected away from HTTPS')
            shutil.copyfileobj(source, output)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def fetch(lock, destination):
    """Authenticate the entire upstream archive before reading archive members."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    archive = destination / 'stream.tar.gz'
    signature = destination / 'stream.tar.gz.minisig'
    download(lock['url'], archive)
    if sha256(archive) != lock['sha256']:
        raise ValueError('Stream source SHA256 mismatch')
    download(lock['signature_url'], signature)
    subprocess.run(['minisign', '-Vm', str(archive), '-x', str(signature), '-P',
                    lock['minisign_public_key']], check=True)
    (destination / 'verification.json').write_text(json.dumps({
        'schema_version': 1, 'sha256': lock['sha256'], 'signature': 'PASS',
        'key_authority': lock['key_authority'], 'version': lock['version']}, indent=2)+'\n')
    return archive


def extract(archive, destination, prefixes=None, exclude=()):
    """Extract regular source files then confined links, including on Python 3.11.

    Optional prefixes select complete component roots and exclude unrelated
    archived build output. They are not source authentication: fetch() must pass.
    """
    import os
    with tarfile.open(archive) as source:
        members = source.getmembers()
        selected = []
        names = set()
        excluded = []
        excluded_roots = tuple(PurePosixPath(path) for path in exclude)
        if any(path.is_absolute() or '..' in path.parts or not path.parts for path in excluded_roots):
            raise ValueError('Invalid generated-output exclusion')
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or '\\' in member.name:
                raise ValueError('Unsafe source archive member path')
            if prefixes and (not name.parts or name.parts[0] not in prefixes):
                continue
            if any(name == path or path in name.parents for path in excluded_roots):
                excluded.append(str(name))
                continue
            if not (member.isdir() or member.isfile() or member.issym() or member.islnk()) or str(name) in names:
                raise ValueError('Unsafe or duplicate source archive member')
            names.add(str(name))
            if member.issym() or member.islnk():
                link = PurePosixPath(member.linkname)
                if link.is_absolute() or '\\' in member.linkname:
                    raise ValueError('External source link')
            selected.append(member)
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=False)
        root = destination.resolve()
        def confined(path):
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise ValueError('Source link escapes extraction root')
            return resolved
        # No links exist during file writes. A conflicting directory/file/link
        # fails rather than replacing or following an earlier archive member.
        for member in selected:
            target = destination / member.name
            confined(target)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as data, target.open('xb') as output:
                    shutil.copyfileobj(data, output)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
        pending = [m for m in selected if m.islnk()]
        while pending:
            remaining = []
            for member in pending:
                target = destination / member.name
                origin = confined(destination / member.linkname)
                if not origin.is_file():
                    remaining.append(member)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                os.link(origin, target, follow_symlinks=False)
            if len(remaining) == len(pending):
                raise ValueError('Missing or cyclic source hard link')
            pending = remaining
        for member in selected:
            if not member.issym():
                continue
            target = destination / member.name
            confined(target.parent / member.linkname)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(member.linkname)
        return {'excluded_generated_members': excluded, 'exclusion_roots': [str(path) for path in excluded_roots]}
