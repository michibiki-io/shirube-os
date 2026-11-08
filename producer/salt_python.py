"""Stage fixed SDK Python sources and the architecture-specific compiler input."""
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit, unquote

from .sources import download, sha256


def prepare(lock_path, architecture, destination, cache=None):
    lock = json.loads(Path(lock_path).read_text())
    if architecture not in {'amd64', 'arm64'}:
        raise ValueError('Unsupported Salt Python input architecture')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    selected = [r for r in lock['components'] if r['name'].startswith(('pypi:', 'build-pypi:'))]
    if not selected or not lock.get('ppbt_assets', {}).get(architecture):
        raise ValueError('Fixed SDK Python source/compiler inputs are missing')
    compiler = lock['ppbt_assets'][architecture]
    records = selected + [{**compiler, 'name': 'compiler:ppbt', 'version': '0.1.8'}]
    seen = set()
    receipt = []
    for record in records:
        parsed = urlsplit(record['url'])
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Only public HTTPS SDK input URLs without credentials are supported')
        name = unquote(parsed.path.rsplit('/', 1)[-1])
        if (not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.+-]*', name)
                or not name.endswith(('.tar.gz', '.tar.bz2', '.tar.xz', '.zip', '.whl'))
                or not re.fullmatch('[0-9a-f]{64}', record['sha256']) or name in seen):
            raise ValueError('Invalid or duplicate fixed SDK Python input')
        seen.add(name)
        if record['name'] == 'compiler:ppbt':
            tag = {'amd64': 'x86_64', 'arm64': 'aarch64'}[architecture]
            if name != 'ppbt-0.1.8-py3-none-manylinux_2_17_' + tag + '.whl' or name != compiler['name']:
                raise ValueError('SDK Python compiler asset architecture differs')
        elif name.endswith('.whl'):
            raise ValueError('SDK Python dependencies must use fixed source archives')
        target = destination / name
        cached = Path(cache) / name if cache else None
        if cached is not None and (cached.exists() or cached.is_symlink()):
            if cached.is_symlink() or not cached.is_file():
                raise ValueError('SDK Python cache must contain regular files')
            shutil.copyfile(cached, target)
        else:
            download(record['url'], target)
        if sha256(target) != record['sha256'] or ('size' in record and target.stat().st_size != record['size']):
            raise ValueError('SDK Python source/compiler input hash or size differs')
        receipt.append({'name': record['name'], 'version': record['version'],
                        'file': name, 'sha256': record['sha256']})
    (destination / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return destination
