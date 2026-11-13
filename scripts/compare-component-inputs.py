#!/usr/bin/env python3
"""Offline base/current manifest comparison; never builds or accesses caches."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

FINGERPRINT = 'sha256:'+'b'*64
STAGES = ('native', 'vpp', 'kea', 'salt', 'runtime', 'kernel')
PROBE = '''
import json, sys
from pathlib import Path
from producer.component_inputs import manifest, outer_key, CACHEABLE
from producer.staged import build_scope
project = Path.cwd()
records = {}
for arch in ('amd64', 'arm64'):
    for stage in ('native', 'vpp', 'kea', 'salt', 'runtime', 'kernel'):
        m = manifest(project, stage, arch)
        scope = build_scope(project, stage, arch, sys.argv[1], {'vpp': 'a'*64} if stage == 'kernel' else {})
        records[arch+'/'+stage] = {'manifest': m, 'scope': scope,
                                 'outer_key': outer_key(scope) if stage in CACHEABLE else None}
print(json.dumps(records, sort_keys=True))
'''


def probe(project):
    return json.loads(subprocess.check_output([sys.executable, '-c', PROBE, FINGERPRINT], cwd=project, text=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='6e09074a425c91748e7fc39c12010ce2d8018837')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    base = subprocess.check_output(['git', 'rev-parse', '--verify', args.base+'^{commit}'], cwd=project, text=True).strip()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='shirube-component-comparison-') as temporary:
        root = Path(temporary)
        archive = root/'base.tar'
        with archive.open('wb') as stream:
            subprocess.run(['git', 'archive', '--format=tar', base], cwd=project, stdout=stream, check=True)
        source = root/'source'; source.mkdir()
        with tarfile.open(archive) as stream:
            stream.extractall(source, filter='data')
        before, after = probe(source), probe(project)
    changes = {}
    for name in before:
        old, new = before[name], after[name]
        oldfiles, newfiles = old['manifest']['files'], new['manifest']['files']
        changes[name] = {
            'base_schema': old['manifest']['schema_version'], 'current_schema': new['manifest']['schema_version'],
            'base_input_id': old['scope']['input_id'], 'current_input_id': new['scope']['input_id'],
            'base_cache_key': old['outer_key'], 'current_cache_key': new['outer_key'],
            'files_added': sorted(newfiles.keys()-oldfiles.keys()),
            'files_removed': sorted(oldfiles.keys()-newfiles.keys()),
            'files_changed': sorted(k for k in oldfiles.keys() & newfiles.keys() if oldfiles[k] != newfiles[k]),
            'toolchain_unchanged': old['manifest']['toolchain'] == new['manifest']['toolchain'],
            'source_values_unchanged': old['manifest']['source_values'] == new['manifest']['source_values'],
            'external_unchanged': old['manifest']['external'] == new['manifest']['external'],
            'catalog_unchanged': old['manifest']['catalog'] == new['manifest']['catalog'],
            'patches_unchanged': old['manifest']['patches'] == new['manifest']['patches'],
        }
    for filename, value in [('base.json', before), ('current.json', after), ('differences.json', {
            'base': base, 'effective_compiler_fingerprint': FINGERPRINT,
            'kernel_predecessor': 'a'*64, 'current': 'working-tree', 'components': changes,
            'compatibility': 'SCHEMA_MIGRATION_NO_OLD_CACHE_REUSE',
            'full_build': 'NOT_RUN', 'hosted_cross_run_hit': 'NOT_RUN'})]:
        (args.output/filename).write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
    print(json.dumps({name: {'changed': c['base_input_id'] != c['current_input_id'],
                             'toolchain_unchanged': c['toolchain_unchanged']} for name,c in changes.items()}, indent=2))


if __name__ == '__main__': main()
