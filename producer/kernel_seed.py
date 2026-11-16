"""Source-only seed coverage hints and event relevance; never compiler-hit proof."""
import json
from pathlib import Path

from .component_inputs import manifest
from .library_cache import toolchain_identity
from .stage_artifacts import digest

PROTOCOL = 1
ARCHITECTURES = ('amd64', 'arm64')
# Trigger inventory is deliberately separate from compile identity. Editing a
# caller/reader asks for a lookup without invalidating unchanged compiler inputs.
ORCHESTRATION = {
    '.github/workflows/release.yml', '.github/workflows/kernel-seed.yml',
    '.github/workflows/validate-staged.yml', '.github/workflows/staged-candidate.yml',
    'scripts/kernel_compile_cache.py', 'scripts/kernel_seed_plan.py',
    'producer/kernel_seed.py', 'scripts/run-native-stage.py',
    'producer/actions.py', 'producer/release_plan.py',
}


def inputs(project, architecture):
    return dict(protocol=PROTOCOL, architecture=architecture,
                kernel=manifest(project, 'kernel', architecture),
                vpp=manifest(project, 'vpp', architecture),
                compiler=toolchain_identity(project, architecture))


def key(project, architecture):
    return digest(inputs(project, architecture))


def inventory(project):
    """Validate dependency graph safety as well as inventory both arch closures."""
    names = set(ORCHESTRATION)
    trees = set()
    for architecture in ARCHITECTURES:
        record = inputs(project, architecture)
        for stage in ('kernel', 'vpp'):
            selected = record[stage]
            names.update(selected['files'])
            names.update(selected['graph'])
            names.update(p['path'] for p in selected['patches'])
            declaration = json.loads((Path(project)/f'containers/native/inputs-{stage}.json').read_text())
            trees.update(declaration['trees'])
    names.update({'source-lock.json', 'containers/native/packages.json',
                  'containers/native/external-sources.lock.json'})
    return names, trees


def relevant(paths, before, after):
    # Callers supply complete changed paths (both sides of renames/deletions).
    old_names, old_trees = inventory(before)
    new_names, new_trees = inventory(after)
    return any(p in old_names | new_names or any(p.startswith(t+'/')
               for t in old_trees | new_trees) for p in paths)


def matrix(architectures):
    selected = list(architectures)
    if not selected or len(set(selected)) != len(selected) or any(a not in ARCHITECTURES for a in selected):
        raise ValueError('Invalid selected kernel seed architectures')
    return {'include': [dict(architecture=a, runner='ubuntu-24.04'+('-arm' if a == 'arm64' else ''))
                        for a in ARCHITECTURES if a in selected]}


def decision(coverage, requested=ARCHITECTURES, *, cold=False):
    matrix(requested)  # Empty/unknown selections must never reach workflow_call.
    selected = [a for a in requested if cold or coverage[a] == 'missing']
    unknown = [a for a in requested if not cold and coverage[a] == 'unknown']
    return dict(coverage=coverage, selected=selected,
                matrix=matrix(selected) if selected else {'include': []},
                status='pending-unknown' if unknown else 'seed-required' if selected else 'covered',
                full_validation='NOT_RUN')
