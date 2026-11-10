"""Validate current GitHub queue syntax not yet supported by actionlint 1.7.12."""
from pathlib import Path
import sys
import yaml


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    seen=set()
    for key, _ in node.value:
        if key.tag=='tag:yaml.org,2002:merge':
            continue
        if not isinstance(key, yaml.ScalarNode):
            raise ValueError('Workflow mapping keys must be scalar names')
        value=key.value
        if value in seen:
            raise ValueError('Duplicate workflow mapping key')
        seen.add(value)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


UniqueLoader.add_constructor('tag:yaml.org,2002:map', unique_mapping)


def validate(workflow, *, release=False):
    if release and (not isinstance(workflow.get('concurrency'), dict)
                    or workflow['concurrency'].get('queue') != 'max'):
        raise ValueError('Release runs must retain multiple pending merges with queue: max')
    groups=[workflow.get('concurrency')]
    groups.extend(job.get('concurrency') for job in workflow.get('jobs', {}).values()
                  if isinstance(job, dict))
    for group in groups:
        if not isinstance(group, dict) or 'queue' not in group:
            continue
        if group['queue'] not in ('single', 'max'):
            raise ValueError('Concurrency queue must be single or max')
        if group['queue']=='max' and group.get('cancel-in-progress', False) is not False:
            raise ValueError('queue: max requires literal cancel-in-progress: false or its default')


if __name__=='__main__':
    for name in sys.argv[1:]:
        path=Path(name)
        validate(yaml.load(path.read_text(), Loader=UniqueLoader), release=path.name=='release.yml')
    print('Workflow queue validation passed')
