"""Small, explicit producer entry points; failures are never build evidence."""
import argparse
import json
from pathlib import Path
from .architecture import preflight, runner
from .sources import fetch


def main():
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest='command', required=True)
    check = subs.add_parser('preflight')
    check.add_argument('architecture', choices=['amd64', 'arm64'])
    check.add_argument('--stage', choices=['iso','release'], default='release')
    check.add_argument('--require-kvm', action='store_true')
    check.add_argument('--storage', type=Path, default=Path('.'))
    acquire = subs.add_parser('fetch-stream')
    acquire.add_argument('--destination', type=Path, required=True)
    acquire.add_argument('--lock', type=Path, default=Path('source-lock.json'))
    labels = subs.add_parser('runner')
    labels.add_argument('architecture', choices=['amd64', 'arm64'])
    labels.add_argument('--setting', default='')
    args = parser.parse_args()
    if args.command == 'preflight':
        preflight(args.architecture, args.storage, minimum_disk_gib=40 if args.stage=='iso' else 160, require_kvm=args.require_kvm)
    elif args.command == 'runner':
        print(json.dumps(runner(args.architecture, args.setting)))
    else:
        fetch(json.loads(args.lock.read_text())['stream'], args.destination)


if __name__ == '__main__':
    main()
