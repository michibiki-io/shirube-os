"""Add dated modification notices while retaining upstream file headers."""
from pathlib import Path

NOTICE = 'Modified by michibiki-io for Shirube OS on 2026-11-08.'


def snapshot(root):
    result = {}
    for path in Path(root).rglob('*'):
        if path.is_file() and not path.is_symlink() and (
                path.suffix in {'.py', '.j2', '.cfg', '.toml'} or path.name == 'build-vyos-image'):
            try:
                result[path] = path.read_text()
            except UnicodeError:
                pass
    return result


def mark_changed(before):
    changed = []
    for path, previous in before.items():
        if not path.is_file() or path.is_symlink():
            continue
        current = path.read_text()
        if current == previous:
            continue
        changed.append(path)
        if NOTICE in current:
            continue
        comment = '{# '+NOTICE+' #}\n' if path.suffix == '.j2' else '# '+NOTICE+'\n'
        if current.startswith('#!'):
            first, separator, rest = current.partition('\n')
            current = first+'\n'+comment+rest
        else:
            current = comment+current
        path.write_text(current)
    return changed
