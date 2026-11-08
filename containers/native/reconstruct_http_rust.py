#!/usr/bin/python3
"""Reconstruct the two pinned HTTP Rust extensions in a native task container.

Acquisition uses fixed public inputs. Each compilation runs in a network-disabled
namespace. Results are component evidence, never an automatic release waiver.
The reference environment is read-only; no shipped wheel or image is replaced.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, '/src')
from producer.architecture import native
from producer.sources import download, extract, sha256
from producer.cargo_sources import inventory, retain


def acquire(record, destination, cache):
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / record['filename']
    cached = cache / path.name
    if cached.exists() or cached.is_symlink():
        if cached.is_symlink() or not cached.is_file():
            raise ValueError('Toolchain cache must contain regular public inputs')
        shutil.copyfile(cached, path)
    else:
        download(record['url'], path)
    if sha256(path) != record['sha256']:
        raise ValueError('HTTP Rust toolchain checksum mismatch')
    return path


def git_vendor(project, retained, registry, work, tools, environment, declaration):
    # Cargo 1.82's vendor operation attempts to remove registry source trees
    # before resolving them again. A directory replacement has no registry
    # re-extraction fallback. Use the read-only reference only after comparing
    # every byte with our fresh reconstruction from checksum-bound archives.
    if not os.statvfs(registry).f_flag & os.ST_RDONLY:
        raise ValueError('Cargo vendoring requires a read-only reference registry')
    def members(root):
        result={}
        for path in root.rglob('*'):
            if path.is_symlink():raise ValueError('Unexpected registry source link')
            if path.is_file():result[str(path.relative_to(root))]=sha256(path)
        return result
    expected=members(retained/'restored/vendor')
    if not expected or members(registry)!=expected:
        raise ValueError('Reference registry differs from the original-archive reconstruction')
    home = work / 'cargo-acquisition'; home.mkdir()
    (home / 'config.toml').write_text(
        '[source.crates-io]\nreplace-with="fixed"\n[source.fixed]\ndirectory='
        + json.dumps(str(registry)) + '\n')
    env = {**environment, 'CARGO_HOME': str(home)}
    subprocess.run([str(tools / 'bin/cargo'), 'fetch', '--locked'], cwd=project, env=env, check=True)
    vendor = work / 'vendor'
    config = subprocess.check_output([str(tools / 'bin/cargo'), 'vendor', '--locked',
        '--respect-source-config', str(vendor)], cwd=project, env=env, text=True)
    # Compare fetched Git package bytes to the independently hash-verified
    # original archive. Check the original manifest in Cargo's Git checkout
    # separately: this Cargo version normalizes the vendored manifest without
    # emitting Cargo.toml.orig.
    for source in declaration['git_sources']:
        archive = retained / 'git-archives' / (source['sha256'] + '.tar.gz')
        if sha256(archive) != source['sha256']:
            raise ValueError('Original Cargo Git archive differs')
        original = work / ('git-original-' + source['sha256'])
        extract(archive, original)
        for name, member in source['packages'].items():
            root = vendor / name
            expected_manifest=original/source['archive_root']/member/'Cargo.toml'
            checkouts=[p for p in (home/'git/checkouts').rglob('Cargo.toml') if p.parent.name==name]
            if len(checkouts)!=1 or sha256(checkouts[0])!=sha256(expected_manifest):
                raise ValueError('Fetched Git manifest differs from the retained fixed commit')
            for file in root.rglob('*'):
                if file.is_symlink():raise ValueError('Unexpected Git vendor link')
                if not file.is_file() or file.name in {'.cargo-checksum.json', 'Cargo.toml'}:continue
                relative = file.relative_to(root)
                expected = original / source['archive_root'] / member / (
                    'Cargo.toml' if str(relative) == 'Cargo.toml.orig' else relative)
                if not expected.is_file() or sha256(file) != sha256(expected):
                    raise ValueError('Git vendor source differs from the retained fixed commit')
    return config


def reconstruct(architecture, reference, work, cache):
    native(architecture)
    reference=reference.resolve();work=work.resolve();cache=cache.resolve()
    work.mkdir(mode=0o700, parents=True, exist_ok=False)
    recipe=Path(__file__).resolve().parent
    lock=json.loads((recipe/'http-rust.lock.json').read_text())
    dependencies=json.loads((recipe/'http-api.lock.json').read_text())
    tools=work/'toolchain';tools.mkdir()
    for name,record in lock['architectures'][architecture].items():
        archive=acquire(record,work/'inputs',cache)
        expanded=work/('install-'+name);extract(archive,expanded)
        roots=list(expanded.iterdir())
        if len(roots)!=1:raise ValueError('Unexpected Rust installer archive root')
        subprocess.run(['sh',str(roots[0]/'install.sh'),'--prefix='+str(tools),
                        '--disable-ldconfig'],check=True)
    acquire(lock['source'],work/'inputs',cache)
    compiler=subprocess.check_output([str(tools/'bin/rustc'),'--version'],text=True).strip()
    if compiler!=lock['rustc_version_output']:raise ValueError('Rust compiler version differs')
    python=reference/'package/usr/share/vyos-http-api-tools/bin/python3'
    if not python.is_file():raise ValueError('A prepared HTTP reference environment is required')
    env={key:value for key,value in os.environ.items() if key not in {
        'CI','RUSTFLAGS','RUSTC_BOOTSTRAP','RUSTUP_TOOLCHAIN','CARGO_ENCODED_RUSTFLAGS',
        'CARGO_HOME','CARGO_NET_OFFLINE','RUSTC_WRAPPER','RUSTC_WORKSPACE_WRAPPER'}}
    env.update(PATH=str(tools/'bin')+':'+env['PATH'],RUSTC=str(tools/'bin/rustc'),
        PYO3_PYTHON=str(python),PYTHON=str(python),CARGO_BUILD_JOBS='4',
        GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null')
    results=[]
    for component in dependencies['packages']:
        if 'cargo_lock' not in component:continue
        name=component['name'];part=work/name;part.mkdir()
        archive=reference/'source/inputs'/component['source']['filename']
        if sha256(archive)!=component['source']['sha256']:raise ValueError('Parent source differs')
        expanded=part/'source';extract(archive,expanded)
        roots=list(expanded.iterdir())
        if len(roots)!=1:raise ValueError('Unexpected parent source archive root')
        project=roots[0]
        declaration={**component['cargo_lock'],'source_sha256':component['source']['sha256']}
        selected=inventory(archive,declaration)
        originals=reference/'source/cargo'/name
        cache_sources=part/'original-inputs';cache_sources.mkdir()
        for item in selected:
            filename=(item['sha256']+'.tar.gz' if 'git_source' in item
                      else item['name'][6:]+'-'+item['version']+'.crate')
            source_input=originals/('git-archives' if 'git_source' in item else 'restored/archives')/filename
            if source_input.is_symlink() or sha256(source_input)!=item['sha256']:
                raise ValueError('Retained Cargo original archive differs')
            if not (cache_sources/filename).exists():shutil.copy2(source_input,cache_sources/filename)
        retained=part/'retained'
        retain(archive,declaration,retained,cache_sources)
        if component['cargo_lock'].get('git_sources'):
            config=git_vendor(project,retained,originals/'restored/vendor',part,tools,env,component['cargo_lock'])
        else:
            config='[source.crates-io]\nreplace-with="fixed"\n[source.fixed]\ndirectory='+json.dumps(str(retained/'restored/vendor'))+'\n'
        home=part/'cargo-offline';home.mkdir();(home/'config.toml').write_text(config+'\n[net]\noffline=true\n')
        compile_env={**env,'CARGO_HOME':str(home),'CARGO_NET_OFFLINE':'true'}
        command=['unshare','--net',str(tools/'bin/cargo'),'build','--locked','--offline','--release']
        if name=='pydantic-core':command+=['--features','pyo3/extension-module']
        subprocess.run(command,cwd=project,env=compile_env,check=True)
        if name=='pydantic-core':
            package=part/'python/pydantic_core';package.parent.mkdir()
            shutil.copytree(project/'python/pydantic_core',package)
            library=project/'target/release/lib_pydantic_core.so';target=package/'_pydantic_core.so'
            probe=('import pydantic_core; assert pydantic_core.__version__=="2.27.1"; '
                   'from pydantic import BaseModel; exec("class Model(BaseModel):\\n value: int"); '
                   'assert Model(value="17").value==17')
        elif name=='watchfiles':
            package=part/'python/watchfiles';package.parent.mkdir()
            shutil.copytree(project/'watchfiles',package)
            library=project/'target/release/lib_rust_notify.so';target=package/'_rust_notify.so'
            probe='''import tempfile,pathlib,threading,time,watchfiles
assert watchfiles.__version__ == '1.0.0'
with tempfile.TemporaryDirectory() as directory:
 path=pathlib.Path(directory)/'source-rebuild-fixture'
 def write():
  time.sleep(0.3)
  path.write_text('native event probe')
 thread=threading.Thread(target=write)
 events=watchfiles.watch(directory,debounce=100,step=50,rust_timeout=3000,yield_on_timeout=True)
 thread.start()
 changes=next(events)
 events.close()
 thread.join()
 assert any(name == str(path) for _,name in changes)
'''
        else:raise ValueError('Unimplemented HTTP Rust component reconstruction')
        shutil.copy2(library,target)
        subprocess.run([str(python),'-c',probe],env={**compile_env,'PYTHONPATH':str(package.parent)},check=True)
        results.append({'name':name,'version':component['version'],'source_sha256':component['source']['sha256'],
            'rebuilt_library_sha256':sha256(target),'compile_network':'disabled namespace','native_probe':'PASS'})
    if len(results)!=2:raise ValueError('HTTP Rust reconstruction scope differs')
    evidence={'schema_version':1,'kind':'diagnostic-http-rust-component-reconstruction','architecture':architecture,
        'status':'PASS','compiler':compiler,'compiler_source_sha256':lock['source']['sha256'],
        'recipe_sha256':sha256(Path(__file__)),'components':results,
        'reference_environment_modified':False,'byte_identical_to_reference_wheels':'NOT_CLAIMED',
        'full_os_reconstruction':'NOT_RUN','release_clearance':'NOT_CLEARED'}
    (work/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print('PASS: both HTTP Rust components rebuilt offline; full release remains uncleared',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('architecture',choices=['amd64','arm64'])
    parser.add_argument('reference',type=Path)
    parser.add_argument('work',type=Path)
    parser.add_argument('--public-input-cache',required=True,type=Path)
    args=parser.parse_args()
    reconstruct(args.architecture,args.reference,args.work,args.public_input_cache)
