"""Commit-based releases for relevant merged PRs; no PR-title classification."""
import re

VERSION_PATTERN=r'v([1-9][0-9]{3})\.(0[1-9]|1[0-2])\.(0|[1-9][0-9]*)'


def stream_parts(version):
    if not isinstance(version,str) or not re.fullmatch(r'[1-9][0-9]{3}\.(?:0[1-9]|1[0-2])',version):
        raise ValueError('Stream version must be YYYY.MM')
    return tuple(map(int,version.split('.')))


def version_parts(version):
    if not isinstance(version,str):raise ValueError('Project version must be vYYYY.MM.REVISION')
    match=re.fullmatch(VERSION_PATTERN,version or '')
    if not match:raise ValueError('Project version must be vYYYY.MM.REVISION')
    return tuple(map(int,match.groups()))


def require_stream_version(version,stream_version):
    if version_parts(version)[:2]!=stream_parts(stream_version):
        raise ValueError('Product release version does not match its pinned Stream baseline')


def relevant(paths):
    # The publication controller removes reviewed non-image paths first.
    # Every remaining path, including unknown root files, requires a release.
    return bool(paths)


def eligible(event_name, event, paths):
    pr = event.get('pull_request', {})
    return (event_name in {'pull_request','pull_request_target'} and event.get('action') == 'closed'
            and pr.get('merged') is True and pr.get('base', {}).get('ref') == 'main'
            and bool(re.fullmatch('[0-9a-f]{40}', pr.get('merge_commit_sha') or ''))
            and relevant(paths))


def next_version(previous, stream_version):
    """Stream selects year/month; every new eligible commit advances revision."""
    current=stream_parts(stream_version)
    if previous is None:return 'v'+stream_version+'.0'
    year,month,revision=version_parts(previous)
    if (year,month)>current:raise ValueError('Stream baseline downgrade cannot create a release')
    return 'v'+stream_version+'.'+str(revision+1 if (year,month)==current else 0)


def release_notes(commits):
    """Commit IDs/messages supplied by git, never a PR title."""
    entries=[]
    for commit in commits:
        entry=f'- {commit["subject"]} ({commit["sha"]})'
        body=commit.get('message','').partition('\n')[2].strip()
        if body:entry+='\n'+ '\n'.join('  '+line for line in body.splitlines())
        entries.append(entry)
    return '\n'.join(entries)+'\n'
