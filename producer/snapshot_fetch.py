"""Transport fallback for debootstrap; its signed-index checksum stays mandatory.

This wrapper never fetches an index from a moving mirror. Only a failed .deb
object under the locked snapshot may be retrieved from the official mirror;
debootstrap must accept it against the original authenticated package digest.
"""
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit


def attempt(arguments):
    process = subprocess.Popen(['wget', *arguments], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    tail = ''
    for line in process.stdout:
        print(line, end='', flush=True)
        tail = (tail + line)[-1024 * 1024:]
    return process.wait(), tail


def fetch(arguments, lock=Path('/src/source-lock.json')):
    for index in range(3):
        status, output = attempt(arguments)
        if status != 4 or index == 2:
            break
        time.sleep(2)
    if status != 8:
        return status
    errors = re.findall(r'ERROR (\d{3}):', output)
    if not errors or any(code not in {'429', '500', '502', '503', '504'} for code in errors):
        return status
    if not arguments or not arguments[-1].startswith(('http://', 'https://')):
        return status
    uri = urlsplit(arguments[-1])
    if (uri.hostname != 'snapshot.debian.org' or uri.username or uri.password
            or uri.port is not None or uri.query or uri.fragment):
        return status
    data = json.loads(Path(lock).read_text())
    roots = [data['debian_snapshot'], data['debian_security_snapshot']]
    roots = [root for root in roots if re.fullmatch(
        r'https://snapshot\.debian\.org/archive/debian(?:-security)?/\d{8}T\d{6}Z/', root)
        and uri.path.startswith(urlsplit(root).path)]
    if len(roots) != 1:
        return status
    root = roots[0]
    filename = unquote(uri.path[len(urlsplit(root).path):])
    path = PurePosixPath(filename)
    if (str(path) != filename or path.is_absolute() or '..' in path.parts
            or not (path.parts[:2] == ('pool', 'main')
                    or path.parts[:3] == ('pool', 'updates', 'main'))
            or not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*_[0-9A-Za-z.+:~%-]+_(amd64|arm64|all)\.deb', path.name)):
        return status
    archive = 'debian-security' if '/debian-security/' in root else 'debian'
    url = 'https://deb.debian.org/' + archive + '/' + filename
    print('Shirube: retrieve identical package object; debootstrap must verify its original signed-index checksum', flush=True)
    return attempt([*arguments[:-1], url])[0]


if __name__ == '__main__':
    raise SystemExit(fetch(sys.argv[1:]))
