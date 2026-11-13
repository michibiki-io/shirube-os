"""vpp compile source/export recipe. Copyright (c) 2026 michibiki.io; MIT."""
import json
from pathlib import Path
import subprocess
from .source_selectors import files, tracked_source_files
from .native_stages import copy_selected, snapshot_files
from .select_vpp import vpp_source_files
from .vpp_sdk import export_vpp_sdk
EXTRAS = {'vpp': ('vpp',)}
ROOTS = {'vpp': ()}
SNAPSHOTS = {}
GIT_SOURCES = {'vpp': ()}

def export_material(stage, sources, packages, destination, architecture):
    material = destination / "material"
    members = copy_selected(vpp_source_files(Path('/extra/vpp/vpp')), material / 'extra/vpp/vpp')
    (material / 'extra/vpp/vpp/shirube-stage-source.json').write_text(json.dumps({'kind': 'tracked', 'members': members}) + '\n')
    copy_selected(tracked_source_files(Path('/extra/vpp/patches')), material / 'extra/vpp/patches')
    multi = subprocess.check_output(['dpkg-architecture', '-qDEB_HOST_MULTIARCH'], text=True).strip()
    export_vpp_sdk(Path('/extra/vpp/vpp'), destination / 'vpp-sdk', multi, architecture)
