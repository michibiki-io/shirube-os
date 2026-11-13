"""Shared verified stage transfer operations; no downstream execution imports."""
import json
from pathlib import Path
import shutil
from .sources import sha256
from .stage_artifacts import digest, unpack

def merge_tree(source, destination):
    """Only equal duplicate files can overlap (e.g. shared builder recipes)."""
    source, destination = Path(source), Path(destination)
    for path in sorted(source.rglob('*')):
        if not path.is_file(): continue
        target = destination / path.relative_to(source)
        if target.exists():
            if sha256(target) != sha256(path): raise ValueError('Conflicting stage material')
            continue
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(path, target)
        target.chmod(path.stat().st_mode & 0o777)

def restore_inputs(directory, destination, stage, architecture, commit, version, compiler, *, dependencies, build_scope):
    directory, destination = Path(directory), Path(destination)
    records = {}; payloads = {}
    wanted={name+suffix for name in dependencies[stage] for suffix in ('.tar','.json')}
    actual={p.name for p in directory.iterdir()}
    if actual!=wanted or any(p.is_symlink() or not p.is_file() for p in directory.iterdir()):
        raise ValueError('Missing/extra or nonregular stage prerequisites')
    for dependency in dependencies[stage]:
        archive = directory / (dependency+'.tar')
        metadata = json.loads((directory / (dependency+'.json')).read_text())
        expected = build_scope(Path('/src'), dependency, architecture, compiler,
                               metadata['scope']['dependencies'])
        target = destination / dependency
        receipt = unpack(archive, target, expected, commit, version, metadata['sha256'])
        if metadata != {**receipt, 'sha256': sha256(archive), 'bytes': archive.stat().st_size}:
            raise ValueError('Stage transfer metadata differs')
        records[dependency] = digest({'input_id': expected['input_id'], 'payload_id': receipt['payload_id']})
        payloads[dependency] = target
    for dependency, payload in payloads.items():
        metadata=json.loads((directory/(dependency+'.json')).read_text())
        for ancestor, identity in metadata['scope']['dependencies'].items():
            if ancestor in records and records[ancestor]!=identity:
                raise ValueError('Stage dependency content differs from supplied predecessor')
    return records, payloads
