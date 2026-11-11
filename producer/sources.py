"""Pinned public acquisition and safe extraction, run inside a task container."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import urllib.request
import urllib.error
import socket
import time
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
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'shirube-os'})
            with urllib.request.urlopen(request, timeout=60) as source, temporary.open('xb') as output:
                if urlsplit(source.url).scheme != 'https':
                    raise ValueError('Input redirected away from HTTPS')
                shutil.copyfileobj(source, output)
            temporary.replace(target)
            return
        except (urllib.error.URLError,TimeoutError,ConnectionResetError) as error:
            retry=(isinstance(error,urllib.error.HTTPError) and error.code in {429,500,502,503,504})
            reason=getattr(error,'reason',error)
            retry=retry or isinstance(reason,(TimeoutError,ConnectionResetError)) or (
                isinstance(reason,socket.gaierror) and reason.errno==socket.EAI_AGAIN)
            if not retry or attempt==2:raise
        finally:
            temporary.unlink(missing_ok=True)
        # Retry the same fixed public GET only; never switch inputs, relax
        # hashes, change DNS, or retry authentication/permission failures.
        time.sleep(attempt+1)


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
    """Stream authenticated sources into private staging, then publish atomically.

    Links are created only after regular writes finish. Late unsafe members or
    link failures leave neither partial destination content nor staging output.
    """
    import os
    import tempfile
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Source destination already exists')
    destination.parent.mkdir(parents=True, exist_ok=True)
    excluded_roots = tuple(PurePosixPath(path) for path in exclude)
    if any(path.is_absolute() or '..' in path.parts or not path.parts for path in excluded_roots):
        raise ValueError('Invalid generated-output exclusion')
    selected_roots = tuple(PurePosixPath(path) for path in prefixes) if prefixes else ()
    if any(path.is_absolute() or '..' in path.parts or not path.parts or '\\' in str(path) for path in selected_roots):
        raise ValueError('Invalid source selection')
    stage = Path(tempfile.mkdtemp(prefix='.shirube-extract-', dir=destination.parent))
    root = stage.resolve()
    def confined(path):
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError('Source link escapes extraction root')
        return resolved
    names, links, excluded = set(), [], []
    try:
        # One sequential decompression pass avoids rewinding large nested gzip
        # archives and retaining metadata for every unrelated component.
        with tarfile.open(archive, mode='r|*') as source:
            for member in source:
                name = PurePosixPath(member.name)
                if name.is_absolute() or '..' in name.parts or '\\' in member.name:
                    raise ValueError('Unsafe source archive member path')
                if selected_roots and not any(name==path or path in name.parents for path in selected_roots):
                    continue
                if any(name == path or path in name.parents for path in excluded_roots):
                    excluded.append(str(name))
                    continue
                if not (member.isdir() or member.isfile() or member.issym() or member.islnk()) or str(name) in names:
                    raise ValueError('Unsafe or duplicate source archive member')
                names.add(str(name))
                target = stage / str(name)
                confined(target)
                if member.issym() or member.islnk():
                    link = PurePosixPath(member.linkname)
                    if link.is_absolute() or '\\' in member.linkname:
                        raise ValueError('External source link')
                    links.append(member)
                elif member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.extractfile(member) as data, target.open('xb') as output:
                        shutil.copyfileobj(data, output)
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
        pending = [member for member in links if member.islnk()]
        while pending:
            remaining = []
            for member in pending:
                target = stage / member.name
                origin = confined(stage / member.linkname)
                if not origin.is_file():
                    remaining.append(member)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                os.link(origin, target, follow_symlinks=False)
            if len(remaining) == len(pending):
                raise ValueError('Missing or cyclic source hard link')
            pending = remaining
        for member in links:
            if member.issym():
                target = stage / member.name
                confined(target.parent / member.linkname)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(member.linkname)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError('Source destination appeared during extraction')
        stage.rename(destination)
        return {'excluded_generated_members': excluded, 'exclusion_roots': [str(path) for path in excluded_roots],
                'selected_roots': sorted(map(str,selected_roots))}
    finally:
        if stage.exists():
            shutil.rmtree(stage)
