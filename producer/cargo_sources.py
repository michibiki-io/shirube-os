"""Retain the complete registry closure declared by an authenticated Cargo.lock.

This is source/notice retention, not a claim that the reference wheel was rebuilt
or that every retained crate has received a distribution-license decision.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile
import tomllib
from urllib.parse import urlsplit, parse_qs

from .debian_sources import legal_files
from .salt_cargo import prepare
from .sources import sha256, download, extract


def inventory(archive, declaration):
    if sha256(archive) != declaration['source_sha256']:
        raise ValueError('Cargo parent source checksum mismatch')
    with tarfile.open(archive) as source:
        matches = [m for m in source.getmembers() if m.name == declaration['member']]
        if len(matches) != 1 or not matches[0].isfile():
            raise ValueError('Cargo parent lock member is missing or ambiguous')
        raw = source.extractfile(matches[0]).read()
    if hashlib.sha256(raw).hexdigest() != declaration['sha256']:
        raise ValueError('Cargo parent lock checksum mismatch')
    lock = tomllib.loads(raw.decode())
    if lock.get('version') != 3:
        raise ValueError('Unsupported Cargo lock format')
    git_sources={item['source']:item for item in declaration.get('git_sources',[])}
    if len(git_sources)!=len(declaration.get('git_sources',[])):
        raise ValueError('Duplicate fixed Cargo Git source')
    used_git=set()
    records = []; local = []; seen = set()
    for package in lock['package']:
        name, version = package['name'], package['version']
        if (not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]*', name)
                or not re.fullmatch('[0-9][A-Za-z0-9.+-]*', version)
                or (name, version) in seen):
            raise ValueError('Invalid or duplicate Cargo lock identity')
        seen.add((name, version))
        if 'source' not in package:
            local.append({'name': name, 'version': version})
            continue
        if package['source'] in git_sources:
            fixed=git_sources[package['source']]
            parsed=urlsplit(package['source'].removeprefix('git+'))
            query=parse_qs(parsed.query)
            if (parsed.scheme!='https' or parsed.hostname!='github.com' or parsed.username or parsed.password
                    or not re.fullmatch('[0-9a-f]{40}',parsed.fragment)
                    or set(query)-{'branch','tag','rev'} or len(query)>1
                    or not re.fullmatch(r'/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git',parsed.path)
                    or fixed['url']!='https://codeload.github.com'+parsed.path[:-4]+'/tar.gz/'+parsed.fragment
                    or not re.fullmatch('[0-9a-f]{64}',fixed['sha256'])):
                raise ValueError('Invalid fixed Cargo Git identity')
            member=fixed['packages'].get(name)
            if not member or Path(member).is_absolute() or '..' in Path(member).parts or '\\' in member:
                raise ValueError('Fixed Cargo Git package path missing or unsafe')
            if fixed['archive_root']!=parsed.path.split('/')[-1][:-4]+'-'+parsed.fragment:
                raise ValueError('Fixed Cargo Git archive root differs')
            used_git.add((package['source'],name))
            records.append({'name':'cargo:'+name,'version':version,'sha256':fixed['sha256'],
                'url':fixed['url'],'git_source':package['source'],'archive_root':fixed['archive_root'],
                'package_path':member})
            continue
        if (package['source'] != 'registry+https://github.com/rust-lang/crates.io-index'
                or not re.fullmatch('[0-9a-f]{64}', package.get('checksum', ''))):
            raise ValueError('Cargo dependency needs an explicit fixed source')
        records.append({'name': 'cargo:' + name, 'version': version,
                        'sha256': package['checksum'],
                        'url': 'https://static.crates.io/crates/' + name + '/' + name + '-' + version + '.crate'})
    if local != [declaration['package']] or not records:
        raise ValueError('Cargo local package differs from its parent source')
    if used_git!={(source,name) for source,record in git_sources.items() for name in record['packages']}:
        raise ValueError('Unused fixed Cargo Git source or package')
    return records


def retain(archive, declaration, destination, cache=None):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    records = inventory(archive, declaration)
    lock = destination / 'registry-inputs.json'
    lock.write_text(json.dumps({'components': [r for r in records if 'git_source' not in r]}, indent=2) + '\n')
    home = prepare(lock, destination / 'restored', cache)
    notices = destination / 'notices'; notices.mkdir()
    result = []
    for record in records:
        crate = record['name'][6:] + '-' + record['version']
        source_member='restored/archives/'+crate+'.crate'
        root = destination / 'restored/vendor' / crate
        if 'git_source' in record:
            source_member='git-archives/'+record['sha256']+'.tar.gz'
            original=destination/source_member
            expanded=destination/'git-sources'/record['sha256']
            if not original.exists():
                original.parent.mkdir(parents=True,exist_ok=True)
                cached=Path(cache)/original.name if cache else None
                if cached is not None and (cached.exists() or cached.is_symlink()):
                    if cached.is_symlink() or not cached.is_file():raise ValueError('Git source cache must be regular')
                    shutil.copy2(cached,original)
                else:download(record['url'],original)
                if sha256(original)!=record['sha256']:raise ValueError('Cargo Git source checksum mismatch')
                extract(original,expanded)
            root=expanded/record['archive_root']
            if sorted(p.name for p in expanded.iterdir())!=[record['archive_root']]:
                raise ValueError('Unexpected Cargo Git archive contents')
            manifest=tomllib.loads((root/record['package_path']/'Cargo.toml').read_text())['package']
            if (manifest['name'],manifest['version'])!=(record['name'][6:],record['version']):
                raise ValueError('Cargo Git package differs from fixed identity')
        notice = notices / (crate + '.txt')
        selected = list(legal_files(root))
        if not selected:
            raise ValueError('Cargo dependency has no original legal files')
        with notice.open('wb') as target:
            target.write(('Component: ' + record['name'] + '@' + record['version'] + '\n').encode())
            for path, member in selected:
                target.write(('\nNotice: ' + member + '\n').encode() + path.read_bytes() + b'\n')
        result.append({**record, 'source_member': source_member,
                       'notice_member': 'notices/' + notice.name,
                       'notice_sha256': sha256(notice)})
    (destination / 'components.json').write_text(json.dumps({
        'parent': declaration, 'components': result,
        'source_retention': 'PASS', 'binary_reconstruction': 'NOT_RUN',
        'license_clearance': 'NOT_CLEARED'}, indent=2) + '\n')
    return home, result
