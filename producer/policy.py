"""Commit-based releases for relevant merged PRs; no PR-title classification."""
import re

RELEVANT = ('producer/', 'containers/', 'source-lock.json', 'patches/', 'guest/',
            'tests/', '.github/workflows/', 'schemas/', 'shirube-os-logo.svg')


def relevant(paths):
    return any(p.startswith(RELEVANT) for p in paths)


def eligible(event_name, event, paths):
    pr = event.get('pull_request', {})
    return (event_name in {'pull_request','pull_request_target'} and event.get('action') == 'closed'
            and pr.get('merged') is True and pr.get('base', {}).get('ref') == 'main'
            and bool(re.fullmatch('[0-9a-f]{40}', pr.get('merge_commit_sha') or ''))
            and relevant(paths))


def next_version(previous, messages):
    if previous is None:
        return 'v0.1.0'
    if not re.fullmatch(r'v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', previous):
        raise ValueError('Previous version must be a stable project semantic version')
    major, minor, patch = map(int, previous[1:].split('.'))
    bump = 1  # Every eligible change receives at least a unique patch version.
    for message in messages:
        header = message.splitlines()[0] if message.splitlines() else ''
        match = re.match(r'([a-z]+)(\([^\r\n()]+\))?(!)?: .+', header)
        if match and (match[3] or re.search(r'^BREAKING[ -]CHANGE: ', message, re.M)):
            bump = 3
        elif match and match[1] == 'feat':
            bump = max(bump, 2)
    if bump == 3:
        return f'v{major+1}.0.0'
    if bump == 2:
        return f'v{major}.{minor+1}.0'
    return f'v{major}.{minor}.{patch+1}'


def release_notes(commits):
    """Commit IDs/messages supplied by git, never a PR title."""
    entries=[]
    for commit in commits:
        entry=f'- {commit["subject"]} ({commit["sha"]})'
        body=commit.get('message','').partition('\n')[2].strip()
        if body:entry+='\n'+ '\n'.join('  '+line for line in body.splitlines())
        entries.append(entry)
    return '\n'.join(entries)+'\n'
