#!/usr/bin/python3
"""Rebuild the pinned headless client from exact public source and vendored inputs.

The offline phase needs only the prepared source/vendor tree and verified SDK.
This component proof does not clear licenses or certify the complete OS image.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,'/src')
from producer.architecture import native, check_elf
from producer.sources import download, extract, sha256


def run(args):
    native(args.architecture)
    lock=json.loads(Path('/src/source-lock.json').read_text())['netbird']
    compiler=lock['build_toolchain']
    work=args.work.resolve()
    source=work/'source'/('netbird-'+lock['source_commit'])
    sdk=work/'sdk/go'
    if args.phase=='prepare':
        work.mkdir(mode=0o700,parents=True,exist_ok=False)
        inputs=work/'inputs';inputs.mkdir()
        for name,record in [('go-sdk.tar.gz',compiler['assets'][args.architecture]),
                            ('go-source.tar.gz',compiler['source']),
                            ('netbird-source.tar.gz',{'url':lock['source_url'],'sha256':lock['source_sha256']})]:
            path=inputs/name;download(record['url'],path)
            if sha256(path)!=record['sha256']:raise ValueError('Reconstruction input checksum mismatch')
        extract(inputs/'netbird-source.tar.gz',work/'source')
        extract(inputs/'go-sdk.tar.gz',work/'sdk')
    elif not (work/'prepared.json').is_file():
        raise ValueError('Prepared source material is required for the offline phase')
    home=work/'home';home.mkdir(exist_ok=True)
    cache=work/(args.phase+'-cache');cache.mkdir(exist_ok=False)
    modules=work/(args.phase+'-modules');modules.mkdir(exist_ok=False)
    env=dict(os.environ,HOME=str(home),PATH=str(sdk/'bin')+':'+os.environ['PATH'],
             GOTOOLCHAIN='local',CGO_ENABLED='0',GOOS='linux',GOARCH=args.architecture,
             GOFLAGS='-p=2',GOMAXPROCS='2',GOMEMLIMIT='768MiB',
             GOCACHE=str(cache),GOMODCACHE=str(modules),
             GOPROXY='https://proxy.golang.org' if args.phase=='prepare' else 'off',
             GOSUMDB='sum.golang.org' if args.phase=='prepare' else 'off')
    before={name:sha256(source/name) for name in ['go.mod','go.sum']}
    if subprocess.check_output([str(sdk/'bin/go'),'version'],text=True,env=env).split()[2]!='go'+compiler['version']:
        raise ValueError('Wrong reconstruction compiler')
    if args.phase=='prepare':
        subprocess.run([str(sdk/'bin/go'),'mod','vendor'],cwd=source,env=env,check=True)
        if before!={name:sha256(source/name) for name in before}:
            raise ValueError('Vendoring changed the pinned dependency declarations')
        (work/'prepared.json').write_text(json.dumps({'source_sha256':lock['source_sha256'],
            'compiler':compiler,'dependency_declarations':before,
            'vendor_modules_sha256':sha256(source/'vendor/modules.txt')},indent=2)+'\n')
    else:
        prepared=json.loads((work/'prepared.json').read_text())
        if (prepared['source_sha256']!=lock['source_sha256'] or prepared['compiler']!=compiler
                or prepared['dependency_declarations']!=before
                or prepared['vendor_modules_sha256']!=sha256(source/'vendor/modules.txt')):
            raise ValueError('Prepared reconstruction inputs changed')
    binary=work/('netbird-'+args.phase)
    subprocess.run([str(sdk/'bin/go'),'build','-mod=vendor','-trimpath','-buildvcs=false',
        '-tags','load_wgnt_from_rsrc','-ldflags',
        '-s -w -X github.com/netbirdio/netbird/version.version='+lock['version'],
        '-o',str(binary),'./client'],cwd=source,env=env,check=True)
    check_elf(binary.read_bytes()[:64],args.architecture)
    if subprocess.check_output([str(binary),'version'],text=True).strip()!=lock['version']:
        raise ValueError('Wrong source-rebuilt client version')
    (work/(args.phase+'-evidence.json')).write_text(json.dumps({'kind':'netbird-component-rebuild',
        'phase':args.phase,'architecture':args.architecture,'version':lock['version'],
        'source_commit':lock['source_commit'],'source_sha256':lock['source_sha256'],
        'compiler':compiler['version'],'binary_sha256':sha256(binary),'build':'PASS',
        'network_required_for_build':args.phase!='offline','byte_reproducibility':'NOT_CLAIMED',
        'licenses':'NOT_CLEARED','complete_os_source_reconstruction':'NOT_RUN'},indent=2)+'\n')
    print('PASS: NetBird component source rebuild ('+args.phase+'); full OS/source/license acceptance remains incomplete')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--architecture',required=True,choices=['amd64','arm64'])
    parser.add_argument('--phase',required=True,choices=['prepare','offline'])
    parser.add_argument('--work',required=True,type=Path)
    run(parser.parse_args())
