"""Bind retained VPP external inputs to complete original binary notices."""
import hashlib
import json
from pathlib import Path
import re
import shutil

from .salt_sources import notice_members
from .sources import download, sha256


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
            preferred=item.get('preferred_sources',[])
            if kind=='bootstrap' and item['filename'].endswith('.whl') and not preferred:
                raise ValueError('VPP bootstrap wheel requires preferred source')
            if preferred and (preferred[0]['name']!=item['name'] or preferred[0]['version']!=item['version']):
                raise ValueError('VPP preferred source version differs from build input')
            for source in preferred:
                if (not re.fullmatch(r'[A-Za-z0-9_.+-]+',source['filename']) or
                        not source.get('legal_notices') or not source['url'].startswith('https://')):
                    raise ValueError('Invalid VPP preferred source declaration')
            result.append({'name':name, 'version':item['version'], 'kind':kind,
                           'source':{k:item[k] for k in ['filename','url','sha256']},
                           'preferred_sources':preferred,
                           'original_legal_files':item['legal_notices']})
    if len({r['name'] for r in result}) != len(result):
        raise ValueError('Duplicate VPP input component')
    if len({r['source']['filename'] for r in result}) != len(result):
        raise ValueError('Duplicate VPP source input filename')
    return external, result


def acquire_preferred(downloads, native, destination, cache=None):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=False)
    _,inputs=declarations(native)
    for record in inputs:
        for source in record['preferred_sources']:
            target=destination/source['filename']
            if not target.exists():
                candidates=[Path(downloads)/source['filename']]
                if cache is not None:candidates.append(Path(cache)/source['filename'])
                original=next((p for p in candidates if p.is_file() and not p.is_symlink()),None)
                if original is None:download(source['url'],target)
                else:shutil.copy2(original,target)
            if target.is_symlink() or sha256(target)!=source['sha256']:
                raise ValueError('Pinned VPP preferred source differs')


def records(downloads, native, preferred=None):
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
        artifact = record['source']; archive = downloads/artifact['filename']
        if archive.is_symlink() or not archive.is_file() or sha256(archive) != artifact['sha256']:
            raise ValueError('Original VPP input differs from pinned source')
        legal = notice_members(archive, record['original_legal_files'])
        parts = [('VPP retained '+record['kind']+' input: '+record['name']+' '+record['version']+'\n'
                  'Original components retain their own copyright and license terms.\n'
                  'Source: '+artifact['url']+'\n').encode()]
        for member, content in sorted(legal.items()):
            parts.append(('\nOriginal notice: '+member+'\n').encode()+content+b'\n')
        for source in record['preferred_sources']:
            if preferred is None:raise ValueError('Missing VPP preferred source directory')
            original=Path(preferred)/source['filename']
            if original.is_symlink() or not original.is_file() or sha256(original)!=source['sha256']:
                raise ValueError('Original VPP preferred source differs')
            contents=notice_members(original,source['legal_notices'])
            if source['sha256']==record['source']['sha256']:continue
            parts.append(('\nPreferred source: '+source['name']+' '+source['version']+'\nSource: '+source['url']+'\n').encode())
            for member,content in sorted(contents.items()):
                parts.append(('\nOriginal notice: '+member+'\n').encode()+content+b'\n')
        content = b''.join(parts)
        name = hashlib.sha256((record['name']+'@'+record['version']+'#'+artifact['sha256']).encode()).hexdigest()+'.copyright'
        notices[name] = content
        result.append({**record,
            'source_identity':record['preferred_sources'][0]['sha256'] if record['preferred_sources'] else artifact['sha256'],
            'notice':{'filename':name, 'sha256':hashlib.sha256(content).hexdigest()}})
    return {'schema_version':1, 'vpp_source_commit':external['source_commit'],
            'vpp_patches_commit':external['patches_commit'],
            'scope':'Retained build inputs; this does not assert every input is linked into every binary.',
            'components':result}, notices


def install(downloads, native, destination, preferred=None):
    destination = Path(destination); destination.mkdir(parents=True, exist_ok=False)
    inventory, notices = records(downloads, native, preferred)
    for name, content in notices.items():
        (destination/name).write_bytes(content)
    (destination/'components.json').write_text(json.dumps(inventory, indent=2)+'\n')
    return inventory


def verify(downloads, native, installed, preferred=None):
    installed = Path(installed)
    expected, notices = records(downloads, native, preferred)
    if (installed.is_symlink() or
            json.loads((installed/'components.json').read_text()) != expected):
        raise ValueError('Installed VPP input/legal inventory differs')
    for name, content in notices.items():
        path = installed/name
        if path.is_symlink() or path.read_bytes() != content:
            raise ValueError('Installed VPP original legal notice is missing or differs')
    return expected
