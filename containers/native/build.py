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
    args = parser.parse_args()
    native(args.architecture)
    if any(args.output.iterdir()):
        raise ValueError('A new empty task output directory is required')
    work = Path('/build-private')
    work.mkdir(mode=0o700)
    lock = json.loads(Path('/src/source-lock.json').read_text())['stream']
    if args.verified_input:
        archive = args.verified_input/'stream.tar.gz'
        if sha256(archive) != lock['sha256']:
            raise ValueError('Cached source archive checksum mismatch')
        subprocess.run(['minisign', '-Vm', str(archive), '-x',
                        str(args.verified_input/'stream.tar.gz.minisig'), '-P', lock['minisign_public_key']], check=True)
    else:
        archive = fetch(lock, work/'input')
    extract(archive, work/'stream')
    nested = work/'stream'/f'vyos-{lock["version"]}-sources/vyos-tarballs/vyos_tarballs.tar.gz'
    extract(nested, work/'sources')
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
    for job in jobs:
        output = work/job['name']
        output.mkdir()
        script = Path('/src/native/package.sh').read_text()
        for old, new in [('/output', str(output)), ('/snapshot', str(sources/job['source'])),
                         ('/stream-build', str(sources/'vyos-build/vyos-build')),
                         ('/local-debs', str(work/'dependencies'))]:
            script = script.replace(old, new)
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
            actual = subprocess.check_output(['dpkg-deb', '-f', str(deb), 'Architecture'], text=True).strip()
            if actual not in {args.architecture, 'all'}:
                raise ValueError('Wrong package architecture')
            certificate = output/'source/certs/signing_key.x509' if job['name'] == 'kernel' else None
            audit(deb, args.architecture, certificate)
            shutil.copy2(deb, exported/deb.name)
            artifacts.append({'name': deb.name, 'sha256': sha256(deb), 'architecture': actual})
        if not artifacts:
            raise ValueError('No package outputs')
        (exported/'manifest.json').write_text(json.dumps({'status':'PASS', 'stage':'package-tranche',
            'source_sha256':lock['sha256'], 'artifacts':artifacts, 'iso':'NOT_BUILT',
            'local_boot':'NOT_RUN', 'oci':'NOT_RUN'}, indent=2)+'\n')
        completed[job['name']] = output


if __name__ == '__main__':
    main()
