"""Public stage CLI with component-selected, graph-audited engine dispatch."""
import importlib
import sys
from .native_stages import COMPILE_STAGES
from .stage_transport import merge_tree, restore_inputs as restore_transfers

DEPENDENCIES = {s: () for s in COMPILE_STAGES}
DEPENDENCIES.update(kernel=('vpp',), integration=('native', 'vpp'),
                    iso=(*COMPILE_STAGES, 'integration'),
                    clearance=('iso',), guests=('clearance',))
STAGES = tuple(DEPENDENCIES)
# Literal component routes are independently resolved by the graph audit. The
# selected compile target must be a declared edge and its entire file is hashed.
ENGINES = {
    'native': 'producer.staged_compile', 'vpp': 'producer.staged_compile',
    'kea': 'producer.staged_compile', 'salt': 'producer.staged_compile',
    'runtime': 'producer.staged_compile', 'kernel': 'producer.staged_compile',
    'integration': 'producer.staged_release', 'iso': 'producer.staged_release',
    'clearance': 'producer.staged_release', 'guests': 'producer.staged_release',
}


def engine(stage):
    if stage not in ENGINES: raise ValueError('Unknown build stage')
    return importlib.import_module(ENGINES[stage])


def build_scope(project, stage, architecture, compiler, dependencies):
    return engine(stage).build_scope(project, stage, architecture, compiler, dependencies)


def restore_inputs(*args):
    return restore_transfers(*args, dependencies=DEPENDENCIES, build_scope=build_scope)


def validate_discard_inputs(*args, **kwargs):
    return engine('iso').validate_discard_inputs(*args, **kwargs)


def __getattr__(name):
    # Preserve the existing downstream API; compile dispatch never calls it.
    if name in {'merged_compile_material', 'link_material', 'integration', 'iso',
                'clearance', 'verify_clearance', 'guests'}:
        return getattr(engine('iso'), name)
    raise AttributeError(name)


def main():
    # Each engine retains argparse validation and the established public argv.
    stage = sys.argv[1] if len(sys.argv) > 1 else 'native'
    if stage in {'-h', '--help'}:
        import argparse
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('stage', choices=STAGES)
        parser.print_help()
        return
    return engine(stage).main()


if __name__ == '__main__': main()
