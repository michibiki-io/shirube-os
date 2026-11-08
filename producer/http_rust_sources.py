"""Bind HTTP extension Rust standard-library sources to the shipped wheel code."""
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import subprocess
import tarfile
import zipfile

from .sources import download, sha256


def retain(lock_path, inputs, destination, cache):
    lock=json.loads(Path(lock_path).read_text());record=lock['source']
    path=Path(inputs)/record['filename'];cached=Path(cache)/record['filename']
    if cached.exists() or cached.is_symlink():
        if cached.is_symlink() or not cached.is_file():raise ValueError('Rust source cache must be regular')
        shutil.copy2(cached,path)
    else:download(record['url'],path)
    if sha256(path)!=record['sha256']:raise ValueError('Rust corresponding source checksum mismatch')
    destination=Path(destination);destination.mkdir()
    notice=destination/'copyright'
    legal=write_notices(path,lock['version'],notice)
    return {'name':'rust-stdlib','version':lock['version'],'source':record,
            'compiler_identity':'rustc version '+lock['rustc_version_output'].removeprefix('rustc '),
            'source_legal_members':legal,'notice_sha256':sha256(notice)}


def write_notices(archive, version, destination):
    seen=set();legal=[];contents={};aliases={}
    root='rustc-'+version+'-src/'
    with tarfile.open(archive,mode='r|*') as source:
        for member in source:
            relative=PurePosixPath(member.name)
            if relative.is_absolute() or '..' in relative.parts or '\\' in member.name:
                raise ValueError('Unsafe Rust source archive member')
            name=relative.name.lower()
            wanted=(any(p.lower()=='licenses' for p in relative.parts[:-1]) or
                    bool(re.fullmatch(r'(?:copying|copyrights?|licen[cs]es?|licensing|notices?)(?:[0-9._-].*)?',name)))
            if not wanted or relative.suffix.lower() in {'.rs','.py','.c','.h','.cpp','.cc','.js','.toml','.json'}:continue
            if member.isdir():continue
            if member.name in seen or not member.name.startswith(root):
                raise ValueError('Rust source legal member is duplicated or outside its source root')
            if member.issym() or member.islnk():
                link=PurePosixPath(member.linkname)
                parent=relative.parent if member.issym() else PurePosixPath('.')
                resolved=posixpath.normpath(str(parent/link))
                if link.is_absolute() or '\\' in member.linkname or not resolved.startswith(root):
                    raise ValueError('Rust legal alias escapes its source root')
                aliases[member.name]=resolved
                seen.add(member.name);legal.append(member.name)
                continue
            if not member.isfile() or not 0<member.size<=4*1024**2:
                raise ValueError('Rust source legal member is not a nonempty regular file or confined alias')
            data=source.extractfile(member).read()
            if not data.strip():continue
            seen.add(member.name);legal.append(member.name);contents[member.name]=data
    if not {root+'LICENSE-MIT',root+'LICENSE-APACHE',root+'COPYRIGHT'}.issubset(seen):
        raise ValueError('Required Rust source legal notices are missing')
    def materialize(name,visiting):
        if name in contents:return contents[name]
        if name in visiting or name not in aliases:
            raise ValueError('Missing or cyclic original Rust legal alias')
        data=materialize(aliases[name],visiting|{name})
        contents[name]=data
        return data
    with Path(destination).open('xb') as target:
        target.write(('Rust compiler/standard-library preferred source '+version+'\n'
                      'Original components retain their individual copyright and license terms.\n').encode())
        for name in legal:
            target.write(('\nOriginal notice: '+name+'\n').encode()+materialize(name,set())+b'\n')
    return legal


def extensions(site, source, components, compiler_identity):
    """Recheck the exact original extension bytes and embedded compiler identity."""
    result=[]
    for record in components:
        if 'cargo' not in record:continue
        directory=record['name'].replace('-','_')
        matches=list((Path(site)/directory).glob('*.so'))
        if len(matches)!=1:raise ValueError('Expected one native Rust extension per HTTP component')
        path=matches[0];relative=str(path.relative_to(site))
        wheel=Path(source)/'inputs'/record['wheel']['filename']
        if sha256(wheel)!=record['wheel']['sha256']:raise ValueError('Reference Rust wheel checksum differs')
        with zipfile.ZipFile(wheel) as archive:
            import hashlib
            original=archive.read(relative)
            if hashlib.sha256(original).hexdigest()!=sha256(path):
                raise ValueError('Installed Rust extension differs from the pinned reference wheel')
        comment=subprocess.check_output(['readelf','-p','.comment',str(path)],text=True,stderr=subprocess.DEVNULL)
        identities=re.findall(r'rustc version [^\n]+',comment)
        if identities!=[compiler_identity]:raise ValueError('Rust compiler source does not match the shipped extension')
        result.append({'component':record['name'],'path':relative,'sha256':sha256(path),
                       'compiler_identity':compiler_identity})
    if len(result)!=2:raise ValueError('Incomplete HTTP Rust extension coverage')
    return result
