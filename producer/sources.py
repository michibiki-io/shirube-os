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


def extract(archive, destination):
    """Reject traversal, external links and special members; preserve safe source links."""
    with tarfile.open(archive) as source:
        members = source.getmembers()
        names = set()
        for member in members:
            name = PurePosixPath(member.name)
            if (name.is_absolute() or '..' in name.parts or '\\' in member.name
                    or not (member.isdir() or member.isfile() or member.issym() or member.islnk()) or str(name) in names):
                raise ValueError('Unsafe or duplicate source archive member')
            names.add(str(name))
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=False)
        if not hasattr(tarfile, 'data_filter'):
            raise ValueError('Python tarfile.data_filter security support is required')
        # Filter again at each extraction, after prior symlinks have been created.
        for member in members:
            source.extract(member, destination, filter='data')
