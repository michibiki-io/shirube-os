"""salt compile source/export recipe. Copyright (c) 2026 michibiki.io; MIT."""
import json
from pathlib import Path
import subprocess
from .source_selectors import files, tracked_source_files
from .native_stages import copy_selected, snapshot_files
EXTRAS = {'salt': ('salt',)}
ROOTS = {'salt': ('salt_v3006.21',)}
SNAPSHOTS = {}
GIT_SOURCES = {'salt': ('salt/salt', 'salt/salt/python-tools-scripts')}

def export_material(stage, sources, packages, destination, architecture):
    material = destination / "material"
    compiler = {'version': subprocess.check_output(['rustc', '--version'], text=True).strip(), 'source': subprocess.check_output(['dpkg-query', '-W', '-f=${source:Package}\t${source:Version}', 'libstd-rust-dev'], text=True).strip()}
    lock = json.loads(Path('/src/native/salt-sources.lock.json').read_text())['rust_compiler']
    if compiler != {'version': lock['version_output'], 'source': lock['source_package'] + '\t' + lock['source_version']}:
        raise ValueError('Actual Salt Rust compiler differs')
    target = material / 'salt-compiler.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(compiler, sort_keys=True) + '\n')
    for location in GIT_SOURCES.get('salt', ()):
        root = Path('/extra') / location
        metadata = {'kind': 'tracked'}
        target = material / 'extra' / location
        metadata['members'] = copy_selected(tracked_source_files(root), target)
        (target / 'shirube-stage-source.json').write_text(json.dumps(metadata) + '\n')
