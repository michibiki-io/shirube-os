"""Authenticate and select only the preferred inputs for unsigned libraries."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, '/src')
from producer.library_cache import eligible
from producer.sources import extract, fetch, sha256


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--verified-input',type=Path)
    args=parser.parse_args()
    if any(args.output.iterdir()):raise ValueError('New library input directory required')
    lock=json.loads(Path('/src/source-lock.json').read_text())['stream']
    if args.verified_input:
        archive=args.verified_input/'stream.tar.gz'
        if sha256(archive)!=lock['sha256']:raise ValueError('Library source archive checksum differs')
        subprocess.run(['minisign','-Vm',str(archive),'-x',str(args.verified_input/'stream.tar.gz.minisig'),
                        '-P',lock['minisign_public_key']],check=True)
    else:archive=fetch(lock,args.output/'download')
    member=f'vyos-{lock["version"]}-sources/vyos-tarballs/vyos_tarballs.tar.gz'
    extract(archive,args.output/'outer',prefixes=[member])
    jobs=json.loads(Path('/src/native/packages.json').read_text())['jobs']
    extract(args.output/'outer'/member,args.output/'sources',
            prefixes=sorted({job['source'].split('/')[0] for job in jobs if eligible(job)}))

    from security_native import prepare_frr
    for job in jobs:
        if eligible(job) and job.get('recipe') == 'frr':
            prepare_frr(Path('/src'), args.output/'sources'/job['source'])


if __name__=='__main__':main()
