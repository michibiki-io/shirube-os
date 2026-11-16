#!/usr/bin/env python3
"""Plan seed work inside the top-level producer queue, bound to immutable source."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from producer.kernel_seed import ARCHITECTURES, decision, key, relevant
from scripts.kernel_compile_cache import API, producer_context, require, find_index


@contextmanager
def snapshot(revision):
    # git supplies repository-owned regular tree content; Python code from this
    # tree is never imported/executed. Graph parsing is by this run's source.
    with tempfile.TemporaryDirectory(prefix='kernel-input-tree-') as temporary:
        root = Path(temporary)
        archive = root/'source.tar'
        with archive.open('wb') as output:
            subprocess.run(['git', 'archive', '--format=tar', revision], stdout=output, check=True)
        source = root/'source'; source.mkdir()
        with tarfile.open(archive) as stream:
            stream.extractall(source, filter='data')
        yield source


def plan(api, source, *, requested=ARCHITECTURES, cold=False, automatic=False,
         changed=(), before=None, current=None, full_planned=False):
    identities = {a: key(source, a) for a in requested}
    result = dict(source_keys=identities, selected=[], matrix={'include': []}, full_validation='NOT_RUN')
    if full_planned:
        return dict(result, status='existing-required-work')
    if automatic:
        require(before is not None)
        if not relevant(changed, before, source):
            return dict(result, status='irrelevant')
        if current is not None and any(key(current, a) != identities[a] for a in requested):
            return dict(result, status='pending-superseded')
    coverage = {a: 'forced-cold' if cold else find_index(api, a, identities[a])[0] for a in requested}
    return dict(result, **decision(coverage, requested, cold=cold))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--architectures', default='amd64,arm64')
    parser.add_argument('--cache-mode', choices=('warm', 'cold'), default='warm')
    parser.add_argument('--automatic', action='store_true')
    parser.add_argument('--full-planned', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    kind = producer_context(os.environ, event, args.commit)  # Before API/git retrieval.
    require(kind == ('automatic-seed' if args.automatic else 'manual-seed'))
    require(subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() == args.commit)
    requested = args.architectures.split(',')
    from producer.kernel_seed import matrix
    matrix(requested)
    identities = {}
    try:
        identities = {a: key(Path('.'), a) for a in requested}
        api = None if args.cache_mode == 'cold' else API(os.environ.get('GH_TOKEN'))
        if args.automatic:
            parent = args.commit+'^1'
            # Diff of actual merged trees includes old and new rename names.
            raw = subprocess.check_output(['git', 'diff', '--name-only', '--no-renames', '-z', parent, args.commit])
            changed = [s.decode() for s in raw.split(b'\0') if s]
            subprocess.run(['git', 'fetch', '--no-tags', 'origin', 'main'], check=True, capture_output=True)
            with snapshot(parent) as before, snapshot('FETCH_HEAD') as current:
                result = plan(api, Path('.'), requested=requested, automatic=True,
                              changed=changed, before=before, current=current, full_planned=args.full_planned)
        else:
            result = plan(api, Path('.'), requested=requested, cold=args.cache_mode == 'cold')
    except Exception:
        # A graph/inventory/API/fetch failure is uncertainty, never absence.
        result = dict(status='pending-unknown', source_keys=identities, input_identity='unavailable' if not identities else 'recorded',
                      selected=[], matrix={'include': []}, full_validation='NOT_RUN')
    result.update(source_commit=args.commit, run_id=os.environ['GITHUB_RUN_ID'],
                  run_attempt=os.environ['GITHUB_RUN_ATTEMPT'], producer_kind=kind,
                  scope='kernel-seed-only', required_full_validation='pending-independent')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2)+'\n')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('selected='+','.join(result['selected'])+'\n')
        output.write('status='+result['status']+'\n')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
        output.write('```json\n'+json.dumps(result, sort_keys=True, indent=2)+'\n```\n')
    if result['status'].startswith('pending-'):
        print('::warning::Kernel seed '+result['status']+'; full validation remains pending.')


if __name__ == '__main__':
    main()
