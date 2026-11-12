"""Retain exact client sources/notices and prove a restored offline rebuild."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile
import os
from .architecture import native
from .bundles import bundle
from .go_modules import build_info,retain_inputs
from .go_notices import go_notice
from .sources import extract,sha256


def inspect(go,binary,strict=True):
    return build_info(subprocess.check_output([str(go),'version','-m',str(binary)],text=True),require_sums=strict)


def internal_package_map(text,source):
    """Map actual selected client files to the upstream directory license scopes."""
    source=Path(source).resolve();decoder=json.JSONDecoder();position=0;packages=[]
    prefix='github.com/netbirdio/netbird/'
    while position<len(text):
        while position<len(text) and text[position].isspace():position+=1
        if position==len(text):break
        item,position=decoder.raw_decode(text,position)
        if not item['ImportPath'].startswith(prefix):continue
        directory=Path(item['Dir']).resolve()
        if not directory.is_relative_to(source):raise ValueError('Client package source escapes retained project')
        relative=directory.relative_to(source)
        if str(relative)!=item['ImportPath'][len(prefix):]:raise ValueError('Client package/source path mismatch')
        selected=[]
        for field in ['GoFiles','CgoFiles','CFiles','HFiles','SFiles','SysoFiles','EmbedFiles']:
            for name in item.get(field,[]):
                path=(directory/name).resolve()
                if not path.is_relative_to(directory) or not path.is_file():raise ValueError('Invalid selected client source file')
                selected.append({'path':str(path.relative_to(source)),'sha256':sha256(path)})
        packages.append({'import':item['ImportPath'],'directory':str(relative),'files':selected})
    if not packages:raise ValueError('Actual headless client package map is empty')
    exceptions={'management','signal','relay','combined'}
    return {'packages':packages,'agpl_directory_packages':[
        item['import'] for item in packages if Path(item['directory']).parts[0] in exceptions]}


def vendor_files(vendor,info,linked):
    """Select linked modules and verify each retained byte against its ZIP."""
    wanted={(record['module'],record['version']) for record in info['modules']}
    origins={}
    for line in (vendor/'modules.txt').read_text().splitlines():
        fields=line.split()
        if len(fields)<3 or fields[0]!='#' or not fields[2].startswith('v'):continue
        origin,version=fields[1:3]
        resolved=(fields[4],fields[5]) if len(fields)==6 and fields[3]=='=>' else (origin,version)
        origins[origin]=resolved
    if not wanted.issubset(set(origins.values())):raise ValueError('Linked module absent from vendor declarations')
    records={(record['module'],record['version']):record for record in json.loads((linked/'modules.json').read_text())['modules']}
    roots=sorted(origins,key=len,reverse=True);opened={};result=[];seen=set()
    try:
        for path in sorted(vendor.rglob('*')):
            if path.is_symlink():raise ValueError('Unexpected link in prepared vendor source')
            if not path.is_file():continue
            relative=str(path.relative_to(vendor))
            if relative=='modules.txt':
                result.append((path,'vendor/'+relative,'sources'));continue
            origin=next((root for root in roots if relative.startswith(root+'/')),None)
            if origin is None:raise ValueError('Vendor file has no declared source module')
            identity=origins[origin]
            if identity not in wanted:continue
            if identity not in opened:opened[identity]=zipfile.ZipFile(linked/records[identity]['source']['path'])
            member=identity[0]+'@'+identity[1]+'/'+relative[len(origin)+1:]
            try:
                with opened[identity].open(member) as stream:expected=hashlib.file_digest(stream,'sha256').hexdigest()
            except KeyError:raise ValueError('Vendor file absent from exact original module source') from None
            if sha256(path)!=expected:raise ValueError('Prepared vendor source differs from original module ZIP')
            seen.add(identity);result.append((path,'vendor/'+relative,'sources'))
    finally:
        for archive in opened.values():archive.close()
    if seen!=wanted:raise ValueError('Incomplete linked module source selection')
    return result


def collect_prepared(binary,asset_archive,arch,prepared,output,restore):
    """Input reuse is allowed only after fixed archive and actual binary checks."""
    native(arch)
    prepared,output,restore=map(Path,(prepared,output,restore))
    lock=json.loads(Path('/src/source-lock.json').read_text())['netbird'];compiler=lock['build_toolchain']
    if sha256(asset_archive)!=lock['assets'][arch]['sha256']:raise ValueError('Wrong reference client asset')
    with tarfile.open(asset_archive) as source:
        with source.extractfile('netbird') as stream:expected_binary=hashlib.file_digest(stream,'sha256').hexdigest()
    if sha256(binary)!=expected_binary:raise ValueError('Installed client differs from the verified public asset')
    output.mkdir(parents=True,exist_ok=False);inputs=output/'inputs';inputs.mkdir()
    expected={'go-sdk.tar.gz':compiler['assets'][arch]['sha256'],
        'go-source.tar.gz':compiler['source']['sha256'],'netbird-source.tar.gz':lock['source_sha256']}
    for name,digest in expected.items():
        path=prepared/'inputs'/name
        if sha256(path)!=digest:raise ValueError('Prepared reconstruction archive differs from source lock')
        shutil.copy2(path,inputs/name)
    # Restore both compiler and project from the exact retained archives, rather
    # than trusting a modified working source tree or a warm compiler cache.
    restore.mkdir(parents=True,exist_ok=False)
    (restore/'inputs').mkdir()
    for path in inputs.iterdir():shutil.copy2(path,restore/'inputs'/path.name)
    extract(inputs/'go-sdk.tar.gz',restore/'sdk')
    go=restore/'sdk/go/bin/go'
    actual=inspect(go,binary)
    if (actual['compiler']!=compiler['version'] or actual['settings'].get('vcs.revision')!=lock['source_commit']
            or actual['settings'].get('CGO_ENABLED')!='0' or actual['settings'].get('GOOS')!='linux'
            or actual['settings'].get('GOARCH')!=arch or actual['settings'].get('-tags')!='load_wgnt_from_rsrc'):
        raise ValueError('Client compiler/source/build mode differs from reconstruction baseline')
    records=retain_inputs(actual,prepared/'prepare-modules',output/'linked')
    vendor=prepared/'source'/('netbird-'+lock['source_commit'])/'vendor'
    if sha256(vendor/'modules.txt')!=lock['vendor_modules_sha256']:
        raise ValueError('Vendor declarations differ from the pinned generated input')
    selected=vendor_files(vendor,actual,output/'linked')
    selected.extend((path,'licenses/'+path.name,'license_files') for path in (output/'linked/notices').iterdir())
    selected.extend([(output/'linked/modules.json','build/linked-modules.json','build_inputs'),
        (Path('/src/native/rebuild_netbird.py'),'build/rebuild_netbird.py','build_inputs')])
    vendor_records=bundle(selected,output/'vendor-source.tar.gz',1773859634)
    (output/'vendor-files.json').write_text(json.dumps(vendor_records,indent=2)+'\n')
    extract(inputs/'netbird-source.tar.gz',restore/'source')
    extract(output/'vendor-source.tar.gz',restore/'restored-vendor')
    source=restore/'source'/('netbird-'+lock['source_commit'])
    if (source/'vendor').exists():raise ValueError('Unexpected vendor tree in original client source')
    shutil.move(str(restore/'restored-vendor/vendor'),source/'vendor')
    declarations={name:sha256(source/name) for name in ['go.mod','go.sum']}
    (restore/'prepared.json').write_text(json.dumps({'source_sha256':lock['source_sha256'],'compiler':compiler,
        'dependency_declarations':declarations,'vendor_modules_sha256':sha256(source/'vendor/modules.txt')},indent=2)+'\n')
    subprocess.run(['unshare','--net','python3','/src/native/rebuild_netbird.py','--architecture',arch,
        '--phase','offline','--work',str(restore)],check=True)
    rebuilt=inspect(go,restore/'netbird-offline',False)
    if {(r['module'],r['version']) for r in rebuilt['modules']}!={(r['module'],r['version']) for r in actual['modules']}:
        raise ValueError('Restored client links different dependency versions')
    environment=dict(os.environ,GOTOOLCHAIN='local',CGO_ENABLED='0',GOOS='linux',GOARCH=arch,
        GOPROXY='off',GOSUMDB='off',GOCACHE=str(restore/'license-map-cache'),
        GOMODCACHE=str(restore/'license-map-modules'),GOMAXPROCS='2',GOMEMLIMIT='768MiB')
    selected=subprocess.check_output([str(go),'list','-deps','-json','-mod=vendor',
        '-tags','load_wgnt_from_rsrc','./client'],cwd=source,env=environment,text=True)
    package_map=internal_package_map(selected,source)
    if package_map['agpl_directory_packages']:
        raise ValueError('Headless client includes directory-license exceptions; additional audit required')
    (output/'internal-packages.json').write_text(json.dumps(package_map,indent=2)+'\n')
    shutil.copy2(restore/'offline-evidence.json',output/'reconstruction.json')
    go_notice(inputs/'go-source.tar.gz',output/'go-copyright')
    result={'schema_version':1,'architecture':arch,'version':lock['version'],
        'reference_binary_sha256':expected_binary,'source_commit':lock['source_commit'],
        'source_sha256':lock['source_sha256'],'compiler':compiler['version'],
        'go_source_sha256':compiler['source']['sha256'],'go_notice_sha256':sha256(output/'go-copyright'),
        'linked_modules':records,'vendor_archive_sha256':sha256(output/'vendor-source.tar.gz'),
        'internal_package_count':len(package_map['packages']),
        'internal_package_map_sha256':sha256(output/'internal-packages.json'),
        'agpl_directory_packages':package_map['agpl_directory_packages'],
        'restored_offline_component_rebuild':'PASS','complete_os_reconstruction':'NOT_RUN',
        'license_clearance':'NOT_CLEARED','byte_reproducibility':'NOT_CLAIMED'}
    (output/'components.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def prepare(binary,asset_archive,arch,work,output):
    work=Path(work)
    subprocess.run(['python3','/src/native/rebuild_netbird.py','--architecture',arch,
        '--phase','prepare','--work',str(work/'prepared')],check=True)
    return collect_prepared(binary,asset_archive,arch,work/'prepared',output,work/'restored')
