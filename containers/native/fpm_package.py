#!/usr/bin/python3
"""Retain actual source/legal/build inputs before packaging runtime extras."""
import json
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
sys.path.insert(0,'/src')
from producer.architecture import native
from producer.go_modules import module_hash,escape,build_info
from producer.sources import download,sha256


def legal(root):
    return [path for path in sorted(Path(root).rglob('*')) if path.is_file() and not path.is_symlink()
            and '.git' not in path.relative_to(root).parts
            and (path.name.lower().startswith(('license','licence','unlicense','copying','copyright','notice','authors','contributors','patents'))
                 or path.name.lower().endswith(('-license','-license.txt','-license.md')))
            and path.stat().st_size and path.stat().st_size<4*1024**2]


def argument(args,*names):
    for index,value in enumerate(args):
        if value in names and index+1<len(args):return args[index+1]
    raise ValueError('Missing required package argument')


def prepare(args):
    name=argument(args,'-n','--name');arch=argument(args,'--architecture')
    native(arch)
    if not re.fullmatch('[a-z0-9][a-z0-9+.-]+',name):raise ValueError('Invalid runtime package name')
    source=Path.cwd().resolve();package=Path(argument(args,'-C','--chdir')).resolve()
    if not source.is_relative_to('/extra') or not package.is_relative_to(source) or not package.is_dir():
        raise ValueError('Runtime packaging must use its private source/staging tree')
    notices=[('Upstream source',legal(source))]
    if not notices[0][1]:raise ValueError('Runtime component has no upstream legal notice')
    inputs=source/'shirube-corresponding-inputs';inputs.mkdir(exist_ok=True)
    records=[];seen=set();compiler_seen=False;binary_reports=[]
    for binary in sorted(package.rglob('*')):
        if not binary.is_file() or binary.is_symlink():continue
        with binary.open('rb') as stream:
            if stream.read(4)!=b'\x7fELF':continue
        result=subprocess.run(['/opt/go/bin/go','version','-m',str(binary)],text=True,
                              stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        lines=result.stdout.splitlines()
        if not lines or ': go' not in lines[0]:continue
        binary_reports.append({'path':str(binary.relative_to(package)),'sha256':sha256(binary),
                               'build_info':build_info(result.stdout,require_sums=False)})
        lock=json.loads(Path('/src/source-lock.json').read_text())['go']
        if not lines[0].endswith('go'+lock['version']):raise ValueError('Unpinned Go compiler in runtime payload')
        if not compiler_seen:
            sdk=inputs/('go'+lock['version']+'.linux-'+arch+'.tar.gz')
            cache=Path('/build-input-cache');cache.mkdir(exist_ok=True)
            original=cache/sdk.name
            if original.is_symlink():raise ValueError('Unexpected Go SDK cache link')
            if not original.exists():download('https://go.dev/dl/'+sdk.name,original)
            if sha256(original)!=lock['sha256'][arch]:raise ValueError('Go reconstruction SDK checksum mismatch')
            shutil.copy2(original,sdk)
            from producer.netbird_sources import go_notice
            standard=inputs/'go-copyright';go_notice(sdk,standard)
            notices.append(('Go standard library/runtime', [standard]))
            records.append({'kind':'go-sdk-with-standard-library-source','version':lock['version'],'file':sdk.name,'sha256':sha256(sdk),
                            'notice':{'file':'go-copyright','sha256':sha256(standard)}})
            compiler_seen=True
        dependencies=[]
        for line in lines[1:]:
            fields=line.split()
            if fields and fields[0]=='dep':dependencies.append(fields[1:])
            elif fields and fields[0]=='=>':
                if not dependencies:raise ValueError('Unmatched Go module replacement')
                dependencies[-1]=fields[1:]
        cache=Path(subprocess.check_output(['/opt/go/bin/go','env','GOMODCACHE'],text=True).strip())
        for fields in dependencies:
            if len(fields)<2:raise ValueError('Local replacement requires an explicit source mapping')
            module,version=fields[:2];identity=(module,version)
            escaped=escape(module)
            if identity in seen:continue
            seen.add(identity)
            vendored=source/'vendor'/module
            if vendored.is_dir():
                paths=legal(vendored)
                record={'kind':'vendored-go-module','module':module,'version':version,
                        'source_path':'vendor/'+module,'vendor_manifest_sha256':sha256(source/'vendor/modules.txt')}
                members=[]
                for path in sorted(vendored.rglob('*')):
                    if path.is_symlink():raise ValueError('Vendored Go source link requires explicit mapping')
                    if path.is_file():members.append({'path':str(path.relative_to(source)),'sha256':sha256(path)})
                if not members:raise ValueError('Empty vendored Go dependency source')
                record['members']=members
                record['sha256']=hashlib.sha256(json.dumps(members,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            else:
                root=cache/(escaped+'@'+version);paths=legal(root)
                archive=cache/'cache/download'/escaped/'@v'/(version+'.zip')
                if len(fields)!=3 or not archive.is_file() or module_hash(archive,module,version)!=fields[2]:
                    raise ValueError('Missing or mismatched actual Go dependency source')
                filename=__import__('hashlib').sha256((module+'@'+version).encode()).hexdigest()+'.zip'
                target=inputs/filename;shutil.copy2(archive,target)
                record={'kind':'go-module','module':module,'version':version,'h1':fields[2],
                        'file':filename,'sha256':sha256(target)}
            notice_root=vendored if vendored.is_dir() else root
            overrides=json.loads(Path('/src/source-lock.json').read_text()).get('go_notice_overrides',{}).get(module+'@'+version,[])
            for override in overrides:
                if 'url' in override:
                    path=inputs/'upstream-notices'/(hashlib.sha256(override['url'].encode()).hexdigest()+'.txt')
                    path.parent.mkdir(exist_ok=True)
                    if not path.exists():download(override['url'],path)
                    base=inputs
                else:
                    path=notice_root/override['path'];base=notice_root
                if not path.resolve().is_relative_to(base.resolve()) or path.is_symlink() or not path.is_file() or sha256(path)!=override['sha256']:
                    raise ValueError('Explicit upstream embedded notice missing or differs')
                if path not in paths:paths.append(path)
            if not paths:raise ValueError('Actual Go dependency has no legal notice: '+module+'@'+version)
            notice=inputs/'notices'/(hashlib.sha256((module+'@'+version).encode()).hexdigest()+'.txt')
            notice.parent.mkdir(exist_ok=True)
            with notice.open('wb') as output:
                for path in paths:
                    output.write(('Notice: '+str(path.relative_to(notice_root if path.is_relative_to(notice_root) else source))+'\n').encode()+path.read_bytes()+b'\n')
            record['additional_upstream_notices']=overrides
            record['notice']={'file':str(notice.relative_to(inputs)),'sha256':sha256(notice)}
            notices.append((module+'@'+version,[notice]));records.append(record)
    (inputs/'binary-info.json').write_text(json.dumps(binary_reports,indent=2)+'\n')
    (inputs/'inventory.json').write_text(json.dumps(records,indent=2)+'\n')
    doc=package/'usr/share/doc'/name;doc.mkdir(parents=True,exist_ok=True)
    with (doc/'copyright').open('wb') as output:
        output.write(b'Packaged for Shirube OS by michibiki-io on 2026-11-08.\nUpstream copyright and license terms are retained below; this does not relicense dependencies.\n')
        for component,paths in notices:
            for path in paths:
                output.write(('\nComponent: '+component+'\nNotice: '+path.name+'\n').encode()+path.read_bytes()+b'\n')
    shutil.copy2(inputs/'inventory.json',doc/'corresponding-inputs.json')
    shutil.copy2(inputs/'binary-info.json',doc/'corresponding-binaries.json')
    subprocess.run(['fpm',*args],check=True)


if __name__=='__main__':prepare(sys.argv[1:])
