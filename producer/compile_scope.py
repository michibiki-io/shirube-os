"""Compile-only scope construction, shared by host and compiler CLI."""
from .native_stages import DEPENDENCIES
from .stage_artifacts import digest, scope


def build_scope(project, stage, architecture, compiler, dependencies):
    if stage not in DEPENDENCIES or set(dependencies) != set(DEPENDENCIES[stage]):
        raise ValueError('Incomplete stage predecessors')
    from .component_inputs import manifest
    return scope(stage, architecture, {'component-manifest-v2': digest(manifest(project, stage, architecture))},
                 compiler, dependencies)
