#!/usr/bin/python3
"""Build the declared Stream package tranche in one private runtime container.

This entry point deliberately does not certify an ISO or a distributable disk.
The package tranche is a producer stage, not the complete runtime dependency set.
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
from producer.packages import audit
from producer.sources import fetch, extract, sha256


def order(jobs):
    catalog = {job['name']: job for job in jobs}
    result, visiting = [], set()
    def visit(name):
        if name in result:
            return
        if name in visiting or name not in catalog:
            raise ValueError('Invalid package dependency graph')
        visiting.add(name)
        for dependency in catalog[name].get('requires', []):
            visit(dependency)
        visiting.remove(name)
        result.append(name)
    for name in catalog:
        visit(name)
    return [catalog[name] for name in result]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('architecture', choices=['amd64', 'arm64'])
    parser.add_argument('--output', type=Path, default=Path('/output'))
    parser.add_argument('--verified-input', type=Path)
    parser.add_argument('--job', action='append')
    parser.add_argument('--target', choices=['packages','iso','release'], default='packages')
    parser.add_argument('--version')
    parser.add_argument('--commit')
    parser.add_argument('--netbird-fixture-stdin',action='store_true')
    parser.add_argument('--include-sources',action='store_true')
    args = parser.parse_args()
    native(args.architecture)
    if args.include_sources and args.target!='iso':
        raise ValueError('Separate source audit is only valid for a complete ISO target')
    management=None
    if args.target=='release':
        if not args.netbird_fixture_stdin:raise ValueError('Full release requires a real local guest fixture')
        from producer.netbird_guest import Management
        management=Management(json.load(sys.stdin))
        # Do not leave private input available to third-party build commands.
        descriptor=os.open(os.devnull,os.O_RDONLY)
        os.dup2(descriptor,0);os.close(descriptor)
    elif args.netbird_fixture_stdin:
        raise ValueError('Guest fixture input is only valid for a full release')
    if any(args.output.iterdir()):
        raise ValueError('A new empty task output directory is required')
    work = Path('/build-private')
    work.mkdir(mode=0o700)
    public_output = args.output
    if args.target in {'iso','release'}:
        import re
        if args.job or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+',args.version or '') or not re.fullmatch('[0-9a-f]{40}',args.commit or ''):
            raise ValueError('Release requires all packages, a project version and exact source commit')
        args.output = work/'package-output'
        args.output.mkdir()
    lock = json.loads(Path('/src/source-lock.json').read_text())['stream']
    if args.verified_input:
        archive = args.verified_input/'stream.tar.gz'
        if sha256(archive) != lock['sha256']:
            raise ValueError('Cached source archive checksum mismatch')
        subprocess.run(['minisign', '-Vm', str(archive), '-x',
                        str(args.verified_input/'stream.tar.gz.minisig'), '-P', lock['minisign_public_key']], check=True)
    else:
        archive = fetch(lock, work/'input')
    nested_member=f'vyos-{lock["version"]}-sources/vyos-tarballs/vyos_tarballs.tar.gz'
    # Source compilation uses this authenticated member, not the unrelated
    # outer Debian source bundle. Avoid writing that large unused input.
    outer_preparation=extract(archive, work/'stream',prefixes=[nested_member])
    nested = work/'stream'/nested_member
    sources = work/'sources'
    catalog = json.loads(Path('/src/native/packages.json').read_text())
    if catalog['source_sha256'] != lock['sha256']:
        raise ValueError('Package catalog does not match verified source')
    completed = {}
    jobs = order(catalog['jobs'])
    if args.job:
        by_name = {job['name']:job for job in jobs}
        selected = set()
        def select(name):
            if name not in by_name:
                raise ValueError('Unknown selected package job')
            if name not in selected:
                selected.add(name)
                for dependency in by_name[name].get('requires', []):
                    select(dependency)
        for name in args.job:
            select(name)
        jobs = [job for job in jobs if job['name'] in selected]
    roots = {job['source'].split('/')[0] for job in jobs} | {'vyos-build'}
    if args.target in {'iso','release'}:
        roots |= {'jool-4.1.9+bf4c7e3669','libpam-radius-auth_1.5.0-cl3u7','humps_v3.8.0','ddclient_debian_3.11.2-1','owamp_v4.4.6','openvpn-otp_9781ff1','udp-broadcast-relay_9a0f13b4b5938bfe0af0d0d4e99dfc127c068e28','node_exporter_v1.9.1','frr_exporter_v1.5.0','blackbox_exporter_v0.26.0','telegraf_v1.28.3','isc-kea_Kea-3.0.2','salt_v3006.21','podman_v4.9.5','ovpn-dco-v0.2.20231117','accel-ppp-ng-3e30d9b','host-sflow_v2.1.21-1'}
    # These signed-archive directories contain previous compiler output, including
    # absolute build-host links. Rebuild them from the adjacent original sources;
    # never follow or rewrite archived external links into the build host.
    preparation = extract(nested, sources, prefixes=roots, exclude=(
        'linux-kernel-6.6.128/linux-6.6.128/debian/linux-headers',
        'accel-ppp-ng-3e30d9b/accel-ppp-ng/build',
    ))
    preparation['authenticated_input']={'archive_sha256':lock['sha256'],
        'nested_member':nested_member,'nested_sha256':sha256(nested),
        'outer_selection':outer_preparation['selected_roots']}
    (args.output/'source-preparation.json').write_text(json.dumps(preparation, indent=2)+'\n')
    from producer.branding import prepare_source_artwork
    prepare_source_artwork(sources,Path('/src/shirube-os-logo.svg'),Path('/src/LICENSE'))
    default=sources/'vyos-1x/vyos-1x/data/config.boot.default'
    if default.is_file():
        console='ttyAMA0' if args.architecture=='arm64' else 'ttyS0'
        default.write_text('// Modified by michibiki-io for Shirube OS on 2026-11-08.\ninterfaces {\n ethernet eth0 { address dhcp }\n loopback lo {}\n}\nsystem {\n host-name shirube-os\n console { device '+console+' { speed 115200 } }\n}\n')
    for job in jobs:
        print('STAGE: native source package '+job['name'],flush=True)
        output = work/job['name']
        output.mkdir()
        from producer.recipes import package_script
        script = package_script(Path('/src/native/package.sh').read_text(),output,
            sources/job['source'],sources/'vyos-build/vyos-build',work/'dependencies')
        dependencies = work/'dependencies'
        if dependencies.exists():
            shutil.rmtree(dependencies)
        dependencies.mkdir()
        for dependency in job.get('requires', []):
            for deb in completed[dependency].glob('*.deb'):
                shutil.copy2(deb, dependencies/deb.name)
        env = dict(os.environ, BUILD_RECIPE=job.get('recipe', 'debian'), STREAM_VERSION=lock['version'],
                   HOST_UID='0', HOST_GID='0')
        subprocess.run(['bash', '-c', script], env=env, check=True)
        exported = args.output/job['name']
        exported.mkdir()
        artifacts = []
        for deb in output.glob('*.deb'):
            if '-build-deps_' in deb.name:
                continue
            name = subprocess.check_output(['dpkg-deb','-f',str(deb),'Package'],text=True).strip()
            if name in job.get('forbidden_binary_packages',[]):
                raise ValueError('Forbidden non-runtime test package was generated')
            actual = subprocess.check_output(['dpkg-deb', '-f', str(deb), 'Architecture'], text=True).strip()
            if actual not in {args.architecture, 'all'}:
                raise ValueError('Wrong package architecture')
            certificate = output/'source/certs/signing_key.x509' if job['name'] == 'kernel' else None
            try:
                audit(deb, args.architecture, certificate)
            except ValueError as error:
                # Audit ValueErrors contain fixed diagnostic text, never payload
                # bytes, enrollment input or private signing-key material.
                print('Package audit rejected '+deb.name+': '+str(error),flush=True)
                raise
            shutil.copy2(deb, exported/deb.name)
            artifacts.append({'name': deb.name, 'sha256': sha256(deb), 'architecture': actual})
        if not artifacts:
            raise ValueError('No package outputs')
        (exported/'manifest.json').write_text(json.dumps({'status':'PASS', 'stage':'package-tranche',
            'source_sha256':lock['sha256'], 'artifacts':artifacts, 'iso':'NOT_BUILT',
            'local_boot':'NOT_RUN', 'oci':'NOT_RUN'}, indent=2)+'\n')
        completed[job['name']] = output
    if args.target == 'iso':
        sys.path.insert(0,'/src/native')
        from image import assemble
        result=assemble(sources,args.output,args.architecture,args.version,public_output)
        image=result['iso']
        (public_output/'ISO-SHA256SUMS').write_text(sha256(image)+'  '+image.name+'\n')
        receipt={'kind':'iso-build','project':'Shirube OS',
            'version':args.version,'source_commit':args.commit,'architecture':args.architecture,
            'upstream_stream_version':'2026.03','source_sha256':lock['sha256'],'source_signature':'PASS',
            'iso':image.name,'sha256':sha256(image),'local_boot':'NOT_RUN','oci':'NOT_RUN',
            'release_clearance':'NOT_CLEARED','source_collection':'NOT_RUN',
            'complete_source_reconstruction':'NOT_RUN','license_clearance':'NOT_RUN'}
        receipt_path=public_output/'iso-build.json'
        receipt_path.write_text(json.dumps(receipt,indent=2)+'\n')
        print('ISO BUILT: '+image.name+' SHA256 '+sha256(image),flush=True)
        if args.include_sources:
            from producer.pipeline import collect_iso_sources
            print('STAGE: ISO source/license audit without VM acceptance',flush=True)
            try:
                _,source=collect_iso_sources(image,args.output,public_output,args.architecture,args.version)
            except Exception:
                receipt['source_collection']='FAIL'
                receipt_path.write_text(json.dumps(receipt,indent=2)+'\n')
                raise
            receipt.update(source_collection='PASS',license_clearance='PASS' if source['cleared'] else 'FAIL',
                source_inventory=source['inventory'].name,source_archives=[path.name for path in source['archives']])
            receipt_path.write_text(json.dumps(receipt,indent=2)+'\n')
    elif args.target == 'release':
        from producer.pipeline import release_candidate
        release_candidate(sources,args.output,public_output,args.architecture,args.version,args.commit,management)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Producer failed ('+type(error).__name__+'); no candidate is cleared for publication',file=sys.stderr)
        raise SystemExit(1) from None
