"""Bind retained VPP external inputs to complete original binary notices."""
import hashlib
import json
from pathlib import Path
import re

from .salt_sources import notice_members
from .sources import sha256


def declarations(native):
    native = Path(native)
    external = json.loads((native/'vpp-external.lock.json').read_text())
    python = json.loads((native/'vpp-python.lock.json').read_text())
    result = []
    for kind, lock in [('native',external), ('bootstrap',python)]:
        if not lock.get('inputs'):
            raise ValueError('Missing complete VPP input group')
        for item in lock['inputs']:
            name = 'vpp-'+kind+':'+item['name']
            if not re.fullmatch(r'[a-z0-9][a-z0-9_.+-]*', item['name']):
                raise ValueError('Invalid VPP input component name')
            if not re.fullmatch(r'[A-Za-z0-9_.+-]+', item['filename']):
                raise ValueError('Unsafe VPP input filename')
            if not item.get('version') or not item.get('legal_notices'):
                raise ValueError('Missing VPP input version or original legal declarations')
            result.append({'name':name, 'version':item['version'], 'kind':kind,
                           'source':{k:item[k] for k in ['filename','url','sha256']},
                           'original_legal_files':item['legal_notices']})
    if len({r['name'] for r in result}) != len(result):
        raise ValueError('Duplicate VPP input component')
    if len({r['source']['filename'] for r in result}) != len(result):
        raise ValueError('Duplicate VPP source input filename')
    return external, result


def records(downloads, native):
    """Return original source-bound notice bytes; no license is inferred."""
    downloads = Path(downloads)
    external, inputs = declarations(native)
    expected={r['source']['filename'] for r in inputs}
    actual={p.name for p in downloads.iterdir()
            if re.search(r'(?:\.tar\.(?:gz|xz|bz2|zst)|\.tgz|\.zip|\.whl)$',p.name)}
    if actual != expected:
        raise ValueError('VPP original source input inventory is incomplete or unexpected')
    result = []; notices = {}
    for record in inputs:
        source = record['source']; archive = downloads/source['filename']
        if archive.is_symlink() or not archive.is_file() or sha256(archive) != source['sha256']:
            raise ValueError('Original VPP input differs from pinned source')
        legal = notice_members(archive, record['original_legal_files'])
        parts = [('VPP retained '+record['kind']+' input: '+record['name']+' '+record['version']+'\n'
                  'Original components retain their own copyright and license terms.\n'
                  'Source: '+source['url']+'\n').encode()]
        for member, content in sorted(legal.items()):
            parts.append(('\nOriginal notice: '+member+'\n').encode()+content+b'\n')
        content = b''.join(parts)
        name = hashlib.sha256((record['name']+'@'+record['version']+'#'+source['sha256']).encode()).hexdigest()+'.copyright'
        notices[name] = content
        result.append({**record, 'notice':{'filename':name, 'sha256':hashlib.sha256(content).hexdigest()}})
    return {'schema_version':1, 'vpp_source_commit':external['source_commit'],
            'vpp_patches_commit':external['patches_commit'],
            'scope':'Retained build inputs; this does not assert every input is linked into every binary.',
            'components':result}, notices


def install(downloads, native, destination):
    destination = Path(destination); destination.mkdir(parents=True, exist_ok=False)
    inventory, notices = records(downloads, native)
    for name, content in notices.items():
        (destination/name).write_bytes(content)
    (destination/'components.json').write_text(json.dumps(inventory, indent=2)+'\n')
    return inventory


def verify(downloads, native, installed):
    installed = Path(installed)
    expected, notices = records(downloads, native)
    if (installed.is_symlink() or
            json.loads((installed/'components.json').read_text()) != expected):
        raise ValueError('Installed VPP input/legal inventory differs')
    for name, content in notices.items():
        path = installed/name
        if path.is_symlink() or path.read_bytes() != content:
            raise ValueError('Installed VPP original legal notice is missing or differs')
    return expected
