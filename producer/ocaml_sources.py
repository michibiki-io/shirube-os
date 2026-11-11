"""Retain the actual OCaml switch inputs used by the native configuration library."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import shutil
from .bundles import bundle
from .sources import sha256


# These packages declare compiler capabilities or system-library checks; their
# metadata is retained, but they contain no additional OCaml runtime source.
VIRTUAL={'base-bigarray','base-threads','base-unix','ocaml','ocaml-config',
    'ocaml-options-vanilla','conf-libffi','conf-libpcre2-8','conf-pkg-config'}


def seed_download_cache(cache,destination):
    """Copy verified public source archives, never Git or compiler workspaces.

    OPAM still checks each selected input against its pinned package metadata.
    Source-only cache misses follow OPAM's ordinary fixed repository recipes.
    """
    cache,destination=map(Path,(cache,destination))
    if not cache.exists():return 0
    if cache.is_symlink() or not cache.is_dir():raise ValueError('Invalid OCaml input cache')
    count=0
    for path in sorted(cache.rglob('*')):
        relative=path.relative_to(cache);algorithm=relative.parts[0]
        if algorithm in {'git','md5'}:continue
        if algorithm not in {'sha256','sha512'}:raise ValueError('Unexpected OCaml source cache digest')
        if path.is_symlink():raise ValueError('OCaml source cache links are not supported')
        if path.is_dir():continue
        digest=path.name
        if (not path.is_file() or len(relative.parts) not in {2,3}
                or not re.fullmatch('[0-9a-f]{'+str(hashlib.new(algorithm).digest_size*2)+'}',digest)
                or len(relative.parts)==3 and relative.parts[1]!=digest[:2]):
            raise ValueError('Invalid OCaml source cache content address')
        with path.open('rb') as stream:actual=hashlib.file_digest(stream,algorithm).hexdigest()
        if actual!=digest:raise ValueError('OCaml source cache checksum mismatch')
        target=destination/relative
        if target.is_symlink() or not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError('Unsafe OCaml source cache destination')
        if target.exists():
            if not target.is_file():raise ValueError('Invalid OCaml source cache destination')
            with target.open('rb') as stream:existing=hashlib.file_digest(stream,algorithm).hexdigest()
            if existing!=digest:raise ValueError('Existing OCaml source cache checksum mismatch')
        else:
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        count+=1
    return count


def retain(root,destination,work,source_files):
    root,destination,work=map(Path,(root,destination,work));work.mkdir()
    listing=subprocess.check_output(['opam','list','--root='+str(root),'--installed',
        '--columns=name,version','--short','--color=never'],text=True)
    switch=root/'4.14.2/.opam-switch'
    inputs=[];components=[];declarations=[]
    for line in listing.splitlines():
        name,version=line.split()
        if not re.fullmatch(r'[a-zA-Z0-9+_.-]+',name+'.'+version):raise ValueError('Unsafe OPAM package identity')
        identity=name+'.'+version
        metadata=switch/'packages'/identity
        if not (metadata/'opam').is_file():raise ValueError('Installed OPAM package metadata missing')
        for path,relative in source_files(metadata):
            inputs.append((path,'opam/metadata/'+identity+'/'+relative,'build_inputs'))
        declarations.append({'name':name,'version':version,'metadata_sha256':sha256(metadata/'opam'),
            'source_required':name not in VIRTUAL})
        if name in VIRTUAL:continue
        candidates=[switch/'sources'/identity,switch/'sources'/name]
        source=next((path for path in candidates if path.is_dir()),None)
        if source is None:raise ValueError('Installed OCaml package source missing: '+identity)
        prefix='opam/sources/'+identity+'/'
        selected=[];notices=[];digest=hashlib.sha256()
        for path,relative in source_files(source):
            member=prefix+relative
            inputs.append((path,member,'sources'));selected.append(member)
            digest.update((sha256(path)+'  '+relative+'\n').encode())
            if path.name.lower().startswith(('license','licence','copying','copyright','notice')):
                notices.append((relative,path))
        if not selected or not notices:raise ValueError('OCaml source or upstream legal notice missing: '+identity)
        notice=work/(identity+'.copyright')
        with notice.open('wb') as stream:
            stream.write(('OCaml switch component: '+identity+'\n').encode())
            for relative,path in notices:
                stream.write(('\nNotice: '+relative+'\n').encode()+path.read_bytes()+b'\n')
        notice_member='opam/notices/'+notice.name
        inputs.append((notice,notice_member,'license_files'))
        components.append({'name':'opam:'+name,'version':version,
            'source_identity':digest.hexdigest(),'source_members':selected,
            'notice':notice,'notice_member':notice_member})
    if not any(item['name']=='opam:vyos1x-config' for item in components):
        raise ValueError('Actual native configuration-library source missing')
    # Keep original downloaded archives/patches as well as the prepared sources.
    # Cache paths are content addresses, not permission to accept changed bytes.
    cache=root/'download-cache'
    if not cache.is_dir():raise ValueError('OCaml source download cache missing')
    for path,relative in source_files(cache):
        algorithm=Path(relative).parts[0]
        # Git-fetched components are retained above as complete source trees and
        # exact package metadata. Repository caches/history are not release inputs.
        if algorithm=='git':continue
        if algorithm not in {'sha256','sha512','md5'}:raise ValueError('Unexpected OPAM source cache digest')
        expected=path.name
        if not re.fullmatch('[0-9a-f]+',expected):raise ValueError('Invalid OPAM source content address')
        with path.open('rb') as stream:actual=hashlib.file_digest(stream,algorithm).hexdigest()
        if actual!=expected:raise ValueError('Corrupt original OCaml source archive')
        inputs.append((path,'opam/download-cache/'+relative,'sources'))
    manifest=work/'installed.json'
    manifest.write_text(json.dumps({'schema_version':1,'packages':declarations,
        'complete_os_reconstruction':'NOT_RUN'},indent=2)+'\n')
    inputs.append((manifest,'opam/installed.json','build_inputs'))
    records=bundle(inputs,destination,1773859634)
    for component in components:
        wanted=set(component.pop('source_members'))
        component['sources']=[ref for ref in records['sources'] if ref['member'] in wanted]
        component['license_files']=[ref for ref in records['license_files'] if ref['member']==component['notice_member']]
    return records,components
