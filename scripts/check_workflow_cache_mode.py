"""Validate literal cache access and local reusable caller caps for actionlint.

Job modes override workflow defaults. An explicit calling job/workflow mode is
both the callee's inherited effective mode and its service-enforced upper bound.
Over-requests are errors even in jobs that would be skipped by an if condition.
This repository intentionally uses only the read/write literal subset.
"""
from pathlib import Path
import sys
import yaml

from scripts.check_workflow_concurrency import UniqueLoader


CACHE_JOBS = {'compiler', 'native', 'vpp', 'kea', 'salt', 'runtime'}


def effective_modes(workflow, *, inherited=None, cap=None):
    def literal(node):
        if 'cache-mode' not in node:
            return None
        mode = node['cache-mode']
        if mode not in ('read', 'write'):
            raise ValueError('cache-mode must be literal read or write')
        if cap == 'read' and mode == 'write':
            raise ValueError('cache-mode write exceeds explicit caller read cap')
        return mode

    default = literal(workflow) or inherited
    return {name: literal(job) or default
            for name, job in workflow.get('jobs', {}).items()}


def validate(workflows):
    for workflow in workflows.values():
        modes = effective_modes(workflow)
        for name, job in workflow.get('jobs', {}).items():
            target = job.get('uses', '').removeprefix('./.github/workflows/')
            if job.get('uses', '').startswith('./.github/workflows/'):
                if target not in workflows:
                    raise ValueError('Missing local reusable workflow: ' + target)
                effective_modes(workflows[target], inherited=modes[name], cap=modes[name])

    staged = workflows['staged-candidate.yml']
    if 'cache-mode' in staged:
        raise ValueError('Staged cache jobs must inherit the caller mode without a workflow default')
    for name, job in staged['jobs'].items():
        if name in CACHE_JOBS:
            if 'cache-mode' in job:
                raise ValueError('Cache jobs must inherit, never request write from a read caller')
        elif job.get('cache-mode') != 'read':
            raise ValueError('Noncache staged jobs must explicitly request read')
    for filename, writers in [('release.yml', {'build'}),
                              ('validate-staged.yml', {'validate-main'})]:
        workflow = workflows[filename]
        if workflow.get('cache-mode') != 'read':
            raise ValueError(filename + ' must default to read')
        modes = effective_modes(workflow)
        if {name for name, mode in modes.items() if mode == 'write'} != writers:
            raise ValueError(filename + ' has unexpected cache writers')
        if any(workflow['jobs'][name].get('cache-mode') != 'write' for name in writers):
            raise ValueError('Cache calling jobs must explicitly request write')


if __name__ == '__main__':
    validate({Path(name).name: yaml.load(Path(name).read_text(), Loader=UniqueLoader)
              for name in sys.argv[1:]})
    print('Workflow cache-mode validation passed')
