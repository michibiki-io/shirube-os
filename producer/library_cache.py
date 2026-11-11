"""Content-bound library cache contexts; release identity and keys are excluded."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
from .sources import sha256


def compiler_fingerprint():
    packages=subprocess.check_output(['dpkg-query','-W','-f=${binary:Package}\t${Version}\t${Architecture}\n'],text=True)
    programs={}
    for name in ['gcc','g++','clang','ld','ar','strip','make','dpkg-deb']:
        path=shutil.which(name)
        if path:programs[name]=sha256(Path(path).resolve())
    if not {'gcc','g++','ld','ar','make','dpkg-deb'}<=programs.keys():raise ValueError('Incomplete native library compiler')
    return 'sha256:'+hashlib.sha256(json.dumps({'packages':sorted(packages.splitlines()),'programs':programs},sort_keys=True).encode()).hexdigest()


def eligible(job):
    name=job.get('name','');source=Path(job.get('source',''))
    return (bool(re.fullmatch(r'(?:lib[a-z0-9][a-z0-9-]*|frr|nftables)',name))
            and len(source.parts)>=2 and not source.is_absolute() and '..' not in source.parts
            and '\\' not in str(source)
            and job.get('recipe','debian') in {'debian','libyang','frr','nftables'})


def toolchain_identity(project,architecture):
    project=Path(project);native=project/'containers/native'
    if not native.is_dir():native=project/'native'
    dockerfile=(native/'Dockerfile').read_text()
    marker='FROM toolchain AS producer\n'
    if dockerfile.count(marker)!=1:raise ValueError('Separate compiler stage required')
    lock=json.loads((project/'source-lock.json').read_text())
    inputs={'compiler_recipe':dockerfile.split(marker)[0],
            'base':lock['container_bases'][architecture],
            'gem_lock':sha256(native/'rubygems.lock.json'),
            'gem_installer':sha256(native/'install_gems.py')}
    return 'sha256:'+hashlib.sha256(json.dumps(inputs,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def tree_identity(root):
    root=Path(root).resolve();result=[]
    for path in sorted(root.rglob('*')):
        relative=path.relative_to(root)
        if '.git' in relative.parts:continue
        if path.is_symlink():
            if not path.resolve().is_relative_to(root):raise ValueError('External library source link')
            result.append({'path':str(relative),'link':str(path.readlink())})
        elif path.is_file():result.append({'path':str(relative),'sha256':sha256(path),'executable':bool(path.stat().st_mode&0o111)})
        elif not path.is_dir():raise ValueError('Special library source input')
    if not result:raise ValueError('Empty preferred library source')
    return result


def identity(project,job,source,dependencies,architecture,toolchain):
    project=Path(project)
    if not eligible(job) or architecture not in {'amd64','arm64'} or not re.fullmatch('sha256:[0-9a-f]{64}',toolchain):
        raise ValueError('Invalid library cache build scope')
    native=project/'containers/native'
    if not native.is_dir():native=project/'native'
    recipe=job.get('recipe','debian')
    paths={'common.sh':native/'library-common.sh','prepare.sh':native/'library-prepare'/(recipe+'.sh'),
           'worker.py':native/'library_worker.py','resources.py':project/'producer/resources.py',
           'Dockerfile':native/'Dockerfile.library'}
    lock=json.loads((project/'source-lock.json').read_text())
    # Image config IDs can change with build timestamps. Cache identity instead
    # binds all fixed compiler-stage inputs, excluding the final producer COPYs.
    record={'schema_version':1,'job':job,'architecture':architecture,'toolchain':toolchain_identity(project,architecture),
            'compiler_fingerprint':toolchain,
            'upstream_stream':lock['stream'],'debian_snapshot':lock['debian_snapshot'],
            'debian_security_snapshot':lock['debian_security_snapshot'],
            'sources':tree_identity(source),'recipes':{name:sha256(path) for name,path in paths.items()},
            'dependencies':sorted([{'name':Path(path).name,'sha256':sha256(path)} for path in dependencies],key=lambda item:item['name'])}
    record['input_id']=hashlib.sha256(json.dumps(record,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return record,paths


def context(project,job,source,dependencies,architecture,toolchain,destination):
    record,paths=identity(project,job,source,dependencies,architecture,toolchain)
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=False)
    shutil.copytree(source,destination/'snapshot',symlinks=True,ignore=shutil.ignore_patterns('.git'))
    (destination/'dependencies').mkdir()
    for path in dependencies:
        if not re.fullmatch('[A-Za-z0-9_.+%-]+\\.deb',Path(path).name):raise ValueError('Invalid cached dependency name')
        shutil.copy2(path,destination/'dependencies'/Path(path).name)
    for name,path in paths.items():shutil.copy2(path,destination/name)
    (destination/'context.json').write_text(json.dumps(record,sort_keys=True,indent=2)+'\n')
    return record


def verify(directory,expected):
    directory=Path(directory)
    receipt_path=directory/'receipt.json'
    if receipt_path.is_symlink():raise ValueError('Library receipt link')
    receipt=json.loads(receipt_path.read_text())
    if (receipt.get('input_id')!=expected['input_id'] or receipt.get('job')!=expected['job']
            or receipt.get('architecture')!=expected['architecture'] or not receipt.get('packages')):
        raise ValueError('Cached library inputs differ')
    members=set()
    for record in receipt['files']:
        member=Path(record['name'])
        if member.is_absolute() or '..' in member.parts or str(member)!=record['name'] or '\\' in str(member) or str(member) in members:
            raise ValueError('Unsafe cached library member')
        members.add(str(member));path=directory/member
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(directory.resolve()) or sha256(path)!=record['sha256']:
            raise ValueError('Cached library member missing or differs')
    actual={str(path.relative_to(directory)) for path in directory.rglob('*') if path.is_file() or path.is_symlink()}
    if actual!=members|{'receipt.json'}:raise ValueError('Uninventoried library cache content')
    for package in receipt['packages']:
        if Path(package['name']).name!=package['name'] or package['name'] not in members or sha256(directory/package['name'])!=package['sha256']:
            raise ValueError('Cached library package differs')
    context_record=json.loads((directory/'context.json').read_text())
    if context_record!=expected:raise ValueError('Cached library source/recipe provenance differs')
    return receipt


def verify_image_archive(path,image,architecture):
    """Reject unrelated tags/files before asking the daemon to load a cache."""
    with tarfile.open(path,'r:*') as archive:
        manifest=archive.getmember('manifest.json')
        if not manifest.isfile() or manifest.size>1024**2:raise ValueError('Invalid library image archive manifest')
        records=json.load(archive.extractfile(manifest))
        if len(records)!=1 or records[0].get('RepoTags')!=[image] or len(records[0].get('Layers',[]))!=1:
            raise ValueError('Library cache must contain one artifact-only image/tag')
        record=records[0];config=json.load(archive.extractfile(record['Config']))
        if config.get('architecture')!=architecture or config.get('os')!='linux' or len(config.get('rootfs',{}).get('diff_ids',[]))!=1:
            raise ValueError('Library image platform or layer shape differs')
        settings=config.get('config',{})
        if (settings.get('Entrypoint') or settings.get('Cmd') or settings.get('Env') not in (None,[],['PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'])
                or settings.get('WorkingDir') not in (None,'','/') or any(settings.get(key) for key in ['Volumes','User','OnBuild','Healthcheck','ExposedPorts'])):
            raise ValueError('Runtime/compiler configuration cannot enter an artifact cache')
        with tarfile.open(fileobj=archive.extractfile(record['Layers'][0]),mode='r|*') as layer:
            for member in layer:
                relative=Path(member.name.removeprefix('./'))
                if (relative.is_absolute() or '..' in relative.parts or not relative.parts
                        or relative.parts[0]!='library' or not (member.isfile() or member.isdir())):
                    raise ValueError('Unrelated state or links in library image layer')


def restore(project,job,source,dependencies,architecture,fingerprint,cache,output):
    """Recheck current inputs and payloads; a miss never reuses older inputs."""
    cache=Path(cache);output=Path(output)
    index=json.loads((cache/'index.json').read_text())
    if index.get('schema_version')!=1 or index.get('architecture')!=architecture:
        raise ValueError('Library cache index architecture/schema differs')
    entry=index['jobs'].get(job['name'])
    if not entry:return None
    if not eligible(job):raise ValueError('Signing/core work cannot be restored from a library cache')
    expected,_=identity(project,job,source,dependencies,architecture,fingerprint)
    if entry.get('input_id')!=expected['input_id']:
        print('LIBRARY CACHE MISS: changed inputs for '+job['name'],flush=True);return None
    directory=cache/job['name'];receipt=verify(directory,expected)
    from .packages import audit
    for package in receipt['packages']:
        if package['architecture'] not in {architecture,'all'}:raise ValueError('Cached binary architecture differs')
        audit(directory/package['name'],architecture)
        shutil.copy2(directory/package['name'],output/package['name'])
    # Preferred original source plus its declared Debian patches and exact
    # retained cache recipes can reconstruct this package independently.
    shutil.copytree(source,output/'source',symlinks=True)
    evidence=output/'cache-build-inputs';evidence.mkdir()
    for name in ['context.json','receipt.json','common.sh','prepare.sh']:
        shutil.copy2(directory/name,evidence/name)
    print('LIBRARY CACHE HIT: '+job['name'],flush=True)
    return {'input_id':expected['input_id'],'compiler_fingerprint':fingerprint,
            'original_resources':receipt['resources']}
