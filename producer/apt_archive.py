"""Cache an identical Debian archive when a snapshot object is unavailable.

The native producer's authenticated, fixed snapshot indexes remain authoritative.
An official mirror supplies bytes only; it cannot select a version or digest.
"""
from email.parser import Parser
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import urllib.error
from urllib.parse import unquote, urlsplit
from .sources import download, sha256


def cache_snapshot_package(package, architecture, snapshot,
                           cache=Path('/var/cache/apt/archives'), expected_filename=None,
                           lists=None, force_cache=False, snapshot_failed=False):
    if (not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*', package)
            or architecture not in {'amd64', 'arm64', 'all'}
            or not re.fullmatch(r'https://snapshot\.debian\.org/archive/debian(?:-security)?/\d{8}T\d{6}Z/', snapshot)):
        raise ValueError('Invalid fixed APT archive scope')
    query = ['apt-cache']
    if lists is not None:
        query += ['-o', 'Dir::State::lists=' + str(lists)]
    output = subprocess.check_output(
        [*query, 'show', '--no-all-versions',
         package if architecture == 'all' else package + ':' + architecture], text=True)
    records = [Parser().parsestr(block) for block in output.strip().split('\n\n')]
    if len(records) != 1:
        raise ValueError('Ambiguous APT archive metadata')
    record = records[0]
    fields = ['Package', 'Version', 'Architecture', 'Size', 'Filename', 'SHA256']
    if any(len(record.get_all(key, [])) != 1 for key in fields):
        raise ValueError('Incomplete or duplicate APT archive fields')
    filename = PurePosixPath(record['Filename'])
    digest = record['SHA256']
    if (record['Package'] != package or record['Architecture'] != architecture
            or not re.fullmatch(r'[0-9A-Za-z.+:~%-]+', record['Version'])
            or not re.fullmatch(r'[0-9a-f]{64}', digest)
            or not record['Size'].isdigit() or not 0 < int(record['Size']) <= 512 * 1024**2
            or filename.is_absolute() or '..' in filename.parts
            or str(filename) != record['Filename']
            or not (filename.parts[:2] == ('pool', 'main')
                    or filename.parts[:3] == ('pool', 'updates', 'main'))
            or not re.fullmatch(re.escape(package) + r'_[0-9A-Za-z.+:~%-]+_'
                                + architecture + r'\.deb', filename.name)):
        raise ValueError('APT archive differs from requested package scope')
    if expected_filename is not None:
        requested = PurePosixPath(expected_filename)
        if (str(requested) != expected_filename or requested.is_absolute()
                or '..' in requested.parts or requested.name != filename.name
                or not (requested.parts[:2] == ('pool', 'main')
                        or requested.parts[:3] == ('pool', 'updates', 'main'))):
            raise ValueError('Failed APT archive differs from authenticated package')
        # Identical package/version/architecture bytes may occur in both the
        # ordinary and security indexes under different pool prefixes. APT's
        # merged record exposes one Filename; retain the failed request's fixed
        # archive root and verify the same signed-index size and SHA256.
        filename = requested
    result = {'package': package, 'version': record['Version'], 'architecture': architecture,
              'sha256': digest, 'size': int(record['Size'])}
    installed = subprocess.run(
        ['dpkg-query', '-W', '-f=${Status}\t${Version}\t${Architecture}', package],
        capture_output=True, text=True, check=False)
    if (not force_cache and installed.returncode == 0 and installed.stdout ==
            'install ok installed\t' + record['Version'] + '\t' + architecture):
        return {**result, 'status': 'ALREADY_INSTALLED'}
    cache = Path(cache)
    if cache.is_symlink() or not cache.is_dir():
        raise ValueError('Regular task APT cache directory required')
    # APT's archive cache includes the epoch, percent-escaping its colon;
    # Debian pool filenames omit that epoch.
    cache_name = package + '_' + record['Version'].replace(':', '%3a') + '_' + architecture + '.deb'
    target = cache / cache_name
    def verify(path):
        if (path.is_symlink() or not path.is_file()
                or path.stat().st_size != result['size'] or sha256(path) != digest):
            raise ValueError('APT archive size or SHA256 mismatch')
    if target.exists() or target.is_symlink():
        verify(target)
        return {**result, 'status': 'VERIFIED_CACHE'}
    with tempfile.TemporaryDirectory(prefix='shirube-apt-', dir=cache) as work:
        staged = Path(work) / filename.name
        url = snapshot + str(filename)
        try:
            if snapshot_failed:
                raise urllib.error.HTTPError(url, 503, 'Previously observed transient object failure', {}, None)
            download(url, staged)
        except urllib.error.HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504}:
                raise
            archive = 'debian-security' if '/debian-security/' in snapshot else 'debian'
            url = 'https://deb.debian.org/' + archive + '/' + str(filename)
            download(url, staged)
        verify(staged)
        # Do not replace another cache entry, even during a concurrent acquisition.
        os.link(staged, target)
    return {**result, 'status': 'VERIFIED_DOWNLOAD', 'url': url}


def _apt(arguments):
    process = subprocess.Popen(['/usr/bin/apt-get', *arguments], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    tail = ''
    for line in process.stdout:
        print(line, end='', flush=True)
        tail = (tail + line)[-1024 * 1024:]
    return process.wait(), tail


def recover_install_failure(output, lock=Path('/src/source-lock.json'), chroot=None):
    """Cache only transient fixed objects; retain the exact authenticated indexes."""
    import json
    denied = ['gpg error', 'no_pubkey', 'is not signed', 'invalid signature',
              'hash sum mismatch', 'unauthenticated packages', 'certificate verification',
              '403 forbidden', '401 unauthorized']
    if any(marker in output.lower() for marker in denied):
        return False
    lines = [line for line in output.splitlines() if line.startswith('E: Failed to fetch ')]
    if not lines:
        return False
    data = json.loads(Path(lock).read_text())
    snapshots = [data['debian_snapshot'], data['debian_security_snapshot']]
    pending = []
    for line in lines:
        match = re.fullmatch(r'E: Failed to fetch (https?://\S+)\s+(429|500|502|503|504)\b.*', line)
        if not match:
            return False
        uri = urlsplit(match[1])
        if (uri.hostname != 'snapshot.debian.org' or uri.username or uri.password
                or uri.port is not None or uri.query or uri.fragment):
            return False
        path = unquote(uri.path)
        roots = [root for root in snapshots if path.startswith(urlsplit(root).path)]
        if len(roots) != 1:
            return False
        root = roots[0]
        filename = path[len(urlsplit(root).path):]
        name = re.fullmatch(r'([a-z0-9][a-z0-9+.-]*)_[0-9A-Za-z.+:~%-]+_(amd64|arm64|all)\.deb',
                            PurePosixPath(filename).name)
        if not name:
            return False
        pending.append((name[1], name[2], root, filename))
    for package, architecture, root, filename in dict.fromkeys(pending):
        options = {'snapshot_failed': True}
        if chroot is not None:
            options.update(cache=Path(chroot)/'var/cache/apt/archives',
                           lists=Path(chroot)/'var/lib/apt/lists', force_cache=True)
        result = cache_snapshot_package(package, architecture, root, expected_filename=filename, **options)
        print('APT fixed snapshot archive: ' + json.dumps(result, sort_keys=True), flush=True)
    return True


def main(arguments=None, lock=Path('/src/source-lock.json')):
    """Retry one failed install only after caching identical authenticated bytes."""
    arguments = sys.argv[1:] if arguments is None else arguments
    status, output = _apt(arguments)
    if status == 100 and 'install' in arguments and recover_install_failure(output, lock):
        return _apt(arguments)[0]
    return status
