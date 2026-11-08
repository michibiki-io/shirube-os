"""Prepare checksum-bound Cargo sources without a mutable registry index."""
import json
from pathlib import Path
import re
import shutil

from .sources import download, extract, sha256


def prepare(lock_path, destination, cache=None):
    lock = json.loads(Path(lock_path).read_text())
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    archives = destination / 'archives'
    archives.mkdir()
    vendor = destination / 'vendor'
    vendor.mkdir()
    selected = [r for r in lock['components'] if r['name'].startswith('cargo:')]
    if not selected:
        raise ValueError('Salt Cargo source inventory is missing')
    seen = set()
    receipts = []
    for record in selected:
        name = record['name'][6:]
        version = record['version']
        if (not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]*', name)
                or not re.fullmatch('[0-9][A-Za-z0-9.+-]*', version)
                or not re.fullmatch('[0-9a-f]{64}', record['sha256'])):
            raise ValueError('Invalid fixed Cargo source identity')
        crate = name + '-' + version
        if crate in seen:
            raise ValueError('Duplicate fixed Cargo source')
        seen.add(crate)
        expected_url = 'https://static.crates.io/crates/' + name + '/' + crate + '.crate'
        if record['url'] != expected_url:
            raise ValueError('Unexpected Cargo source URL')
        archive = archives / (crate + '.crate')
        cached = Path(cache) / archive.name if cache else None
        if cached is not None and (cached.exists() or cached.is_symlink()):
            if cached.is_symlink() or not cached.is_file():
                raise ValueError('Cargo input cache must contain regular files')
            shutil.copyfile(cached, archive)
        else:
            download(record['url'], archive)
        if sha256(archive) != record['sha256']:
            raise ValueError('Cargo source SHA256 mismatch')
        staging = destination / ('extract-' + crate)
        extract(archive, staging)
        if sorted(p.name for p in staging.iterdir()) != [crate] or not (staging / crate).is_dir():
            raise ValueError('Cargo archive root differs from fixed identity')
        root = staging / crate
        files = {}
        for path in sorted(root.rglob('*')):
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise ValueError('Cargo vendor source contains a link or special file')
            if path.is_file():
                relative = str(path.relative_to(root))
                if relative == '.cargo-checksum.json':
                    raise ValueError('Cargo source unexpectedly contains vendor checksums')
                files[relative] = sha256(path)
        if 'Cargo.toml' not in files:
            raise ValueError('Cargo source manifest is missing')
        (root / '.cargo-checksum.json').write_text(json.dumps(
            {'package': record['sha256'], 'files': files}, sort_keys=True) + '\n')
        root.rename(vendor / crate)
        staging.rmdir()
        receipts.append({'name': name, 'version': version, 'sha256': record['sha256'],
                         'files': len(files)})
    home = destination / 'cargo-home'
    home.mkdir()
    (home / 'config.toml').write_text(
        '[source.crates-io]\nreplace-with = "shirube-fixed-sources"\n\n'
        '[source.shirube-fixed-sources]\ndirectory = ' + json.dumps(str(vendor.resolve()))
        + '\n\n[net]\noffline = true\n')
    (destination / 'receipt.json').write_text(json.dumps(receipts, indent=2) + '\n')
    return home


def patch_sdk_environment(source, home, python_inputs=None):
    """Keep registry policy local to Salt's two SDK installation functions."""
    import ast
    source = Path(source)
    path = source / 'tools/pkg/build.py'
    text = path.read_text()
    tree = ast.parse(text)
    insertions = []
    for name in ['onedir_dependencies', 'salt_onedir']:
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
        if len(functions) != 1:
            raise ValueError('Upstream Salt SDK function differs')
        assignments = [node for node in ast.walk(functions[0])
                       if isinstance(node, ast.Assign) and len(node.targets) == 1
                       and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'env'
                       and ast.unparse(node.value) == 'os.environ.copy()']
        if len(assignments) != 1:
            raise ValueError('Upstream Salt SDK environment differs')
        assignment = assignments[0]
        indent = ' ' * assignment.col_offset
        insertions.append((assignment.end_lineno,
                           indent + '# Modified by michibiki-io 2026-11-08: fixed offline Rust inputs.\n'
                           + indent + 'env["CARGO_HOME"] = ' + json.dumps(str(home)) + '\n'
                           + indent + 'env["CARGO_NET_OFFLINE"] = "true"\n'
                           + (indent + 'env["PIP_NO_INDEX"] = "1"\n'
                              + indent + 'env["PIP_FIND_LINKS"] = ' + json.dumps(str(python_inputs)) + '\n'
                              if python_inputs is not None else '')))
        if python_inputs is not None:
            bootstrap = [node for node in ast.walk(functions[0])
                         if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                         and node.func.attr == 'run'
                         and any(isinstance(arg, ast.Constant) and arg.value == 'relenv[toolchain]'
                                 for arg in node.args)]
            if len(bootstrap) != 1 or any(k.arg == 'env' for k in bootstrap[0].keywords):
                raise ValueError('Upstream SDK compiler bootstrap differs')
            call = bootstrap[0]
            insertions.append((call.end_lineno - 1, ' ' * (call.col_offset + 4) + 'env=env,\n'))
    lines = text.splitlines(keepends=True)
    for index, content in sorted(insertions, reverse=True):
        lines.insert(index, content)
    result = ''.join(lines)
    ast.parse(result)
    path.write_text(result)
