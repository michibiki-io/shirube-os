#!/usr/bin/python3
"""Own MIT packaging for the pinned HTTP API Python dependency environment.

No upstream wrapper/setup/maintainer code is copied. The dependency versions
are facts from the authenticated Stream requirement baseline; each upstream
wheel/source archive and its own legal notices are retained separately.
"""
import argparse
import csv
import base64
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
sys.path.insert(0,'/src')
from producer.architecture import native
from producer.sources import download, sha256
from producer.packages import audit
from producer.cargo_sources import retain as retain_cargo
from producer.http_rust_sources import retain as retain_rust, extensions as rust_extensions

NAME='vyos-http-api-tools'
INSTALL='/usr/share/'+NAME
LEGAL=('license','licence','copying','copyright','notice','authors')


def legal_inputs(archive):
    result=[]
    def wanted(name):
        return len(Path(name).parts)<=4 and Path(name).name.lower().startswith(LEGAL)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                if not member.is_dir() and wanted(member.filename):
                    result.append((member.filename,source.read(member)))
    else:
        with tarfile.open(archive) as source:
            for member in source:
                if member.isfile() and wanted(member.name):
                    result.append((member.name,source.extractfile(member).read()))
    return [(name,data) for name,data in result if data.strip()]


def build(arch,output):
    native(arch)
    output=Path(output);source=output/'source';source.mkdir()
    lock_path=Path('/src/native/http-api.lock.json')
    lock=json.loads(lock_path.read_text())
    if lock['python']!='3.11' or sys.version_info[:2]!=(3,11):raise ValueError('Python ABI mismatch')
    for file in [lock_path,Path('/src/native/http_api.py'),Path('/src/native/http-rust.lock.json'),
                 Path('/src/native/reconstruct_http_rust.py'),Path('/src/LICENSE')]:
        shutil.copy2(file,source/file.name)
    inputs=source/'inputs';inputs.mkdir()
    package=output/'package';environment=package/INSTALL.lstrip('/')
    environment.parent.mkdir(parents=True)
    subprocess.run([sys.executable,'-m','venv','--without-pip','--system-site-packages',str(environment)],check=True)
    # Remove only activation helpers that embed the temporary staging directory.
    for name in ['activate','activate.csh','activate.fish','Activate.ps1']:
        (environment/'bin'/name).unlink(missing_ok=True)
    (environment/'pyvenv.cfg').write_text('home = /usr/bin\ninclude-system-site-packages = true\nversion = 3.11\nexecutable = /usr/bin/python3.11\n')
    requirements=[];records=[];notices=[]
    for component in lock['packages']:
        wheels=component['wheels'];wheel=wheels[arch];sdist=component['source']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',component['name']):raise ValueError('Invalid dependency name')
        for record in [wheel,sdist]:
            name=record['filename']
            if Path(name).name!=name or name in {'.','..'} or '\\' in name or not re.fullmatch('[0-9a-f]{64}',record['sha256']):raise ValueError('Invalid dependency input')
            path=inputs/name;cached=Path('/public-inputs')/name
            if cached.is_file() and not cached.is_symlink():
                if sha256(cached)!=record['sha256']:raise ValueError('Cached dependency checksum mismatch')
                shutil.copy2(cached,path)
            else:
                download(record['url'],path)
            if sha256(path)!=record['sha256']:raise ValueError('Python dependency checksum mismatch')
        legal=legal_inputs(inputs/wheel['filename'])+legal_inputs(inputs/sdist['filename'])
        if not legal:raise ValueError('Python dependency has no usable legal notice')
        notices.append((component['name']+'=='+component['version'],legal))
        requirements.append(component['name']+'=='+component['version']+' --hash=sha256:'+wheel['sha256'])
        record={'name':component['name'],'version':component['version'],'wheel':wheel,'source':sdist}
        if 'cargo_lock' in component:
            declaration={**component['cargo_lock'],'source_sha256':sdist['sha256']}
            _,crates=retain_cargo(inputs/sdist['filename'],declaration,
                source/'cargo'/component['name'],Path('/public-inputs'))
            record['cargo']={'parent':declaration,'components':crates}
        records.append(record)
    rust=retain_rust(Path('/src/native/http-rust.lock.json'),inputs,source/'rust-notices',Path('/public-inputs'))
    requirements_file=source/'requirements.lock';requirements_file.write_text('\n'.join(requirements)+'\n')
    subprocess.run([sys.executable,'-m','pip','install','--break-system-packages','--no-index',
        '--find-links',str(inputs),'--only-binary=:all:','--ignore-installed','--no-deps','--no-compile',
        '--require-hashes','--prefix',str(environment),'-r',str(requirements_file)],check=True)
    # Debian's system pip uses its local/dist-packages prefix scheme. Normalize
    # that staging scheme to the persistent virtual environment layout.
    site=environment/'lib/python3.11/site-packages'
    local=environment/'local'
    for origin,target in [(local/'lib/python3.11/dist-packages',site),(local/'bin',environment/'bin')]:
        if origin.is_dir():
            for child in origin.iterdir():
                if (target/child.name).exists() or (target/child.name).is_symlink():raise ValueError('Conflicting environment payload')
                shutil.move(str(child),target/child.name)
    if local.exists():
        if any(path.is_file() or path.is_symlink() for path in local.rglob('*')):
            raise ValueError('Unexpected dependency files outside the normalized environment')
        shutil.rmtree(local)
    rust['extensions']=rust_extensions(site,source,records,rust['compiler_identity'])
    for file in (environment/'bin').iterdir():
        if file.is_file() and not file.is_symlink():
            data=file.read_bytes()
            if data.startswith(b'#!'):
                file.write_bytes(('#!'+INSTALL+'/bin/python3\n').encode()+data.partition(b'\n')[2])
    # Update RECORD hashes for generated entry points with their final shebang.
    for record in site.glob('*.dist-info/RECORD'):
        rows=[]
        with record.open(newline='') as stream:
            for row in csv.reader(stream):
                path=(site/row[0]).resolve()
                if not path.is_relative_to(environment.resolve()):raise ValueError('Wheel RECORD escapes environment')
                if path.is_file() and path!=record.resolve():
                    data=path.read_bytes();row[1]='sha256='+base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=');row[2]=str(len(data))
                rows.append(row)
        with record.open('w',newline='') as stream:csv.writer(stream).writerows(rows)
    doc=package/'usr/share/doc'/NAME;doc.mkdir(parents=True)
    rust_notice=doc/'third-party/rust-stdlib/copyright';rust_notice.parent.mkdir(parents=True)
    shutil.copy2(source/'rust-notices/copyright',rust_notice)
    rust['license_notice']={'path':'third-party/rust-stdlib/copyright','sha256':sha256(rust_notice)}
    with (doc/'copyright').open('wb') as target:
        target.write(b'HTTP API dependency environment packaging: Copyright (c) 2026 michibiki-io.\nModified/created 2026-11-08. MIT License for this packaging only.\n\n')
        target.write(Path('/src/LICENSE').read_bytes())
        target.write(b'\nDependency baseline: public VyOS Stream 2026.03 requirements.\nUpstream dependencies retain their own notices and license terms below.\n')
        target.write(b'\nRust standard-library source legal notices: third-party/rust-stdlib/copyright\n')
        for component,legal in notices:
            name=component.partition('==')[0]
            path=doc/'third-party'/name/'copyright';path.parent.mkdir(parents=True)
            with path.open('wb') as upstream:
                upstream.write(('Component: '+component+'\n').encode())
                for member,data in legal:
                    upstream.write(('\nNotice: '+member+'\n').encode()+data+b'\n')
            next(record for record in records if record['name']==name)['license_notice']={
                'path':'third-party/'+name+'/copyright','sha256':sha256(path)}
            target.write(('\nComponent: '+component+'\nLicense/copyright: third-party/'+name+'/copyright\n').encode())
        for record in records:
            for crate in record.get('cargo',{}).get('components',[]):
                crate_name=crate['name'][6:]+'-'+crate['version']
                member='third-party/'+record['name']+'/cargo/'+crate_name+'/copyright'
                path=doc/member;path.parent.mkdir(parents=True)
                shutil.copy2(source/'cargo'/record['name']/crate['notice_member'],path)
                if sha256(path)!=crate['notice_sha256']:raise ValueError('Cargo notice changed during packaging')
                crate['license_notice']={'path':member,'sha256':sha256(path)}
                target.write(('\nComponent: '+crate['name']+'@'+crate['version']+'\nLicense/copyright: '+member+'\n').encode())
    (doc/'components.json').write_text(json.dumps({'schema_version':1,'dependency_baseline':lock['dependency_baseline'],
        'architecture':arch,'components':records,'rust_stdlib':rust},indent=2)+'\n')
    control=package/'DEBIAN';control.mkdir()
    metadata='Package: '+NAME+'\nSource: shirube-http-api-tools\nVersion: '+lock['version']+'\nArchitecture: '+arch+'\nMaintainer: michibiki-io <maintainers@michibiki.io>\nDepends: python3 (>= 3.11), python3 (<< 3.12), libpam0g\nDescription: Shirube OS HTTP API Python dependency environment\n'
    debian=source/'debian';debian.mkdir()
    (debian/'control').write_text('Source: shirube-http-api-tools\nMaintainer: michibiki-io <maintainers@michibiki.io>\n\n'+metadata)
    elf=[]
    for path in site.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream:
                if stream.read(4)==b'\x7fELF':elf.extend(['-e',str(path)])
    if not elf:raise ValueError('Expected native API dependency extensions')
    dependencies=subprocess.check_output(['dpkg-shlibdeps','-O','--ignore-missing-info',*elf],cwd=source,text=True)
    shared=next((line.partition('=')[2] for line in dependencies.splitlines() if line.startswith('shlibs:Depends=')),None)
    if not shared:raise ValueError('Native extension runtime dependencies were not resolved')
    metadata=metadata.replace('python3 (<< 3.12), libpam0g','python3 (<< 3.12), libpam0g, '+shared)
    (control/'control').write_text(metadata)
    deb=output/(NAME+'_'+lock['version']+'_'+arch+'.deb')
    subprocess.run(['dpkg-deb','--root-owner-group','-b',str(package),str(deb)],check=True)
    audit(deb,arch)
    print('PASS: own HTTP API environment packaging; component license clearance remains separately required')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('architecture',choices=['amd64','arm64'])
    parser.add_argument('output',type=Path)
    args=parser.parse_args();build(args.architecture,args.output)
