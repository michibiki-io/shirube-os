"""vpp compile source/export recipe. Copyright (c) 2026 michibiki.io; MIT."""
import json
from pathlib import Path
import subprocess
from .source_selectors import files, tracked_source_files
from .native_stages import copy_selected, snapshot_files
from .select_vpp import vpp_source_files
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


def export_vpp_sdk(source, destination, multiarch, architecture):
    """Accel needs generated and upstream API headers beyond the four VPP DEBs."""
    source, destination = Path(source), Path(destination)
    # Export only header files from these precise SDK roots. Link libraries are
    # already supplied by libvppinfra/vpp DEBs and extracted by accel().
    roots = {'source': source / 'src',
             'generated': source / 'build-root/build-vpp-native/vpp/CMakeFiles/vpp-api'}
    counts = {}
    for label, root in roots.items():
        selected = [(p, str(p.relative_to(root))) for p in sorted(root.rglob('*.h')) if p.is_file()]
        if any(not p.resolve().is_relative_to(source.resolve()) for p,_ in selected):
            raise ValueError('External VPP SDK header link')
        counts[label] = len(copy_selected(selected, destination / label))
    install = source / 'build-root/install-vpp-native/vpp/lib' / multiarch
    libraries = [(p.resolve(), p.name) for p in sorted(install.glob('*.so*')) if p.is_file()]
    if not libraries: raise ValueError('VPP SDK link libraries missing')
    if any(not p.is_relative_to(source.resolve()) for p, _ in libraries): raise ValueError('External SDK library link')
    from .architecture import check_elf
    for path,_ in libraries:check_elf(path.read_bytes(),architecture)
    copy_selected(libraries, destination / 'lib')
    (destination / 'sdk.json').write_text(json.dumps({'schema_version': 1, 'multiarch': multiarch,
        'header_counts': counts, 'libraries': [name for _, name in libraries]}, sort_keys=True) + '\n')
