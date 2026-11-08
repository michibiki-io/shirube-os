"""Bind packaged Go dependency notices to retained, exact reconstruction inputs."""
import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath
from .sources import sha256
from .sanitize import path_in_root
from .go_modules import build_info


def dependencies(rootfs,source,name):
    source=Path(source)
    inputs=source/'shirube-corresponding-inputs'
    inventory=inputs/'inventory.json'
    installed=path_in_root(rootfs,'usr/share/doc/'+name+'/corresponding-inputs.json')
    if not inventory.is_file() or not installed.is_file() or inventory.read_bytes()!=installed.read_bytes():
        raise ValueError('Installed runtime source inventory missing or differs')
    records=json.loads(inventory.read_text());children=[];seen=set()
    def checked(member,digest):
        path=PurePosixPath(member)
        if path.is_absolute() or '..' in path.parts or str(path)!=member or '\\' in member:
            raise ValueError('Unsafe runtime source input path')
        target=inputs/member
        if not target.resolve().is_relative_to(inputs.resolve()) or target.is_symlink() or not target.is_file() or sha256(target)!=digest:
            raise ValueError('Runtime dependency source/notice missing or differs')
        return target
    for record in records:
        kind=record['kind'];version=record['version']
        component='go-stdlib' if kind=='go-sdk-with-standard-library-source' else 'go:'+record['module']
        identity=(component,version)
        if identity in seen:raise ValueError('Duplicate runtime source dependency')
        seen.add(identity)
        notice=checked(record['notice']['file'],record['notice']['sha256'])
        member_prefix='source/shirube-corresponding-inputs/'
        child={'name':component,'version':version,'source_identity':record['sha256'],
               'notice':notice,'notice_member':member_prefix+record['notice']['file']}
        if kind=='vendored-go-module':
            members=record['members']
            if not members or hashlib.sha256(json.dumps(members,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=record['sha256']:
                raise ValueError('Vendored source member inventory differs')
            paths=set();refs=[]
            for member in members:
                relative=PurePosixPath(member['path']);base=PurePosixPath('vendor')/record['module']
                if relative.is_absolute() or '..' in relative.parts or base not in relative.parents or str(relative) in paths:
                    raise ValueError('Invalid vendored source member')
                paths.add(str(relative));target=source/relative
                if not target.resolve().is_relative_to(source.resolve()) or target.is_symlink() or not target.is_file() or sha256(target)!=member['sha256']:
                    raise ValueError('Vendored source member missing or differs')
                refs.append({'member':'source/'+str(relative),'sha256':member['sha256']})
            child['source_members']=refs
        elif kind in {'go-module','go-sdk-with-standard-library-source'}:
            checked(record['file'],record['sha256'])
            child.update(source_member=member_prefix+record['file'],source_sha256=record['sha256'])
        else:raise ValueError('Unknown runtime source dependency kind')
        children.append(child)
    reports=inputs/'binary-info.json'
    shipped=path_in_root(rootfs,'usr/share/doc/'+name+'/corresponding-binaries.json')
    if not reports.is_file() or not shipped.is_file() or reports.read_bytes()!=shipped.read_bytes():
        raise ValueError('Installed runtime binary/source binding missing or differs')
    actual_dependencies=set();compilers=set();paths=set()
    for report in json.loads(reports.read_text()):
        if report['path'] in paths:raise ValueError('Duplicate runtime binary binding')
        paths.add(report['path']);binary=path_in_root(rootfs,report['path'])
        if not binary.is_file() or sha256(binary)!=report['sha256']:
            raise ValueError('Installed runtime Go binary differs from retained source binding')
        actual=build_info(subprocess.check_output(['/opt/go/bin/go','version','-m',str(binary)],text=True),require_sums=False)
        if actual!=report['build_info']:raise ValueError('Installed runtime Go build information differs')
        actual_dependencies.update(('go:'+item['module'],item['version']) for item in actual['modules'])
        compilers.add(actual['compiler'])
    expected_dependencies={(item['name'],item['version']) for item in children if item['name']!='go-stdlib'}
    expected_compilers={item['version'] for item in children if item['name']=='go-stdlib'}
    if actual_dependencies!=expected_dependencies or compilers!=expected_compilers:
        raise ValueError('Runtime source inventory does not cover actual linked dependencies')
    return children
