"""kea compile source/export recipe. Copyright (c) 2026 michibiki.io; MIT."""
import json
from pathlib import Path
import subprocess
from .source_selectors import files, tracked_source_files
from .native_stages import copy_selected, snapshot_files
from .select_kea import kea_source_files
EXTRAS = {'kea': ('kea',)}
ROOTS = {'kea': ('isc-kea_Kea-3.0.2',)}
SNAPSHOTS = {}
GIT_SOURCES = {'kea': ()}

def export_material(stage, sources, packages, destination, architecture):
    material = destination / "material"
    tool = json.loads(Path('/src/source-lock.json').read_text())['kea_build_tool']
    root = Path('/extra/kea/isc-kea')
    members = copy_selected(kea_source_files(root, sources / 'isc-kea_Kea-3.0.2/isc-kea', Path('/extra/kea/packaging'), tool), material / 'extra/kea/isc-kea')
    (material / 'extra/kea/isc-kea/shirube-stage-source.json').write_text(json.dumps({'kind': 'kea', 'members': members}) + '\n')
