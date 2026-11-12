"""Source-local expiry exception for locked snapshots in the ISO builder only."""
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys


# Official live-build_20230502.tar.xz, functions/sourcelist.sh (not vendored).
# https://deb.debian.org/debian/pool/main/l/live-build/live-build_20230502.tar.xz
SOURCELIST_SHA256 = '3b22c35af39430f903c6288ef822bd141d6b07accba2ea6413eface6f5cb53da'
ANCHOR = '\t# Disable deb-src entries?\n'
OPTION = 'check-valid-until=no'


def snapshot_roots(lock):
    data = json.loads(Path(lock).read_text())
    roots = {data[key] for key in ('debian_snapshot', 'debian_security_snapshot')}
    return roots | {uri.replace('https://', 'http://', 1) for uri in roots}


def rewrite_sources(path, lock):
    """Validate all targeted entries before writing; preserve all other bytes."""
    path = Path(path)
    roots = snapshot_roots(lock)
    original = path.read_bytes().decode('utf-8')
    rewritten = []
    for line in original.splitlines(keepends=True):
        content = line.split('#', 1)[0]
        # Recognize complete URI tokens even in malformed option blocks, so
        # broken targeted input cannot silently evade validation.
        uris = re.findall(r'https?://[^\s\[\]]+', content)
        if not re.match(r'^\s*deb(?:-src)?\s', content) or not roots.intersection(uris):
            rewritten.append(line)
            continue
        match = re.match(r'^([ \t]*deb(?:-src)?[ \t]+)(?:\[([^\[\]]*)\][ \t]+)?'
                         r'(\S+)([ \t]+[^\r\n]+)(\r?\n)?$', line)
        if match and match[3] not in roots and re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', match[3]):
            # A locked URI elsewhere in an unrelated entry is not its source URI.
            rewritten.append(line)
            continue
        if not match or match[3] not in roots:
            raise ValueError('Malformed targeted APT source entry')
        fields = match[4].split('#', 1)[0].split()
        if not fields or (len(fields) < 2 and not fields[0].endswith('/')):
            raise ValueError('Missing targeted APT suite/components')
        options = match[2]
        if options is not None:
            tokens = options.split()
            if not tokens or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]*[+-]?=[^\s=\[\]#]+',
                                                  token) for token in tokens):
                raise ValueError('Malformed targeted APT options')
            validity = [token for token in tokens
                        if token.split('=')[0].rstrip('+-').lower() == 'check-valid-until']
            if validity and validity != [OPTION]:
                raise ValueError('Conflicting targeted APT validity option')
            if validity:
                rewritten.append(line)
                continue
            offset = match.end(2)
            line = line[:offset] + ' ' + OPTION + line[offset:]
        else:
            offset = match.end(1)
            line = line[:offset] + '[' + OPTION + '] ' + line[offset:]
        rewritten.append(line)
    result = ''.join(rewritten)
    if result != original:
        path.write_bytes(result.encode('utf-8'))


def configure_sources(lock=Path('/src/source-lock.json')):
    """Patch the installed common generator once, inside a disposable container."""
    files = subprocess.check_output(['dpkg-query', '--listfiles', 'live-build'], text=True)
    candidates = [Path(line) for line in files.splitlines()
                  if line.endswith('/live/build/functions/sourcelist.sh')]
    if len(candidates) != 1 or not candidates[0].is_absolute():
        raise ValueError('Missing or ambiguous live-build sourcelist implementation')
    path = candidates[0]
    original = path.read_bytes()
    text = original.decode('utf-8')
    if (hashlib.sha256(original).hexdigest() != SOURCELIST_SHA256
            or text.count(ANCHOR) != 1
            or text.count('Create_apt_sources_list ()\n{') != 1):
        raise ValueError('Unexpected or already modified live-build sourcelist implementation')
    # Explicit exit propagates errors even if the caller does not enable set -e.
    hook = ('\t# Modified by michibiki-io: source-local locked snapshot expiry exception.\n'
            '\tpython3 ' + shlex.quote(str(Path(__file__).resolve())) + ' '
            + shlex.quote(str(Path(lock).resolve()))
            + ' "${PARENT_LIST_FILE}" || exit $?\n\n')
    path.write_bytes(text.replace(ANCHOR, hook + ANCHOR).encode('utf-8'))


if __name__ == '__main__':
    rewrite_sources(sys.argv[2], sys.argv[1])
