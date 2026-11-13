"""Declared physical compile graph and independent static dependency audit.

The graph is data, never executable configuration. All input identities hash whole
files. Static analysis checks literal imports/paths; declared dynamic edges cover
recipe dispatch and source-selected inputs (see STAGED-BUILD for limits).
"""
import ast
from functools import lru_cache
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys

COMPONENTS = ('native', 'vpp', 'kea', 'salt', 'runtime', 'kernel')


def input_path(project, name):
    project = Path(project)
    if (project / 'containers/native').is_dir():
        return project / name
    if name == 'containers/native' or name.startswith('containers/native/'):
        return project / 'native' / name[len('containers/native'):].lstrip('/')
    if name == 'patches/vyos' or name.startswith('patches/vyos/'):
        return project / 'vyos-patches' / name[len('patches/vyos'):].lstrip('/')
    if name == 'patches/kernel' or name.startswith('patches/kernel/'):
        return project.parent / 'kernel-patches' / name[len('patches/kernel'):].lstrip('/')
    return project / name


def regular(project, name, *, directory=False):
    """Reject aliases at every ancestor, including Docker's mapped parent trees."""
    logical = PurePosixPath(name)
    if logical.is_absolute() or '..' in logical.parts or str(logical) != name.rstrip('/'):
        raise ValueError('Unsafe declared input: '+name)
    path = input_path(project, name)
    for parent in (path, *path.parents):
        try:
            mode = parent.lstat().st_mode
        except OSError as error:
            raise ValueError('Missing declared input: '+name) from error
        if stat.S_ISLNK(mode):
            raise ValueError('Symlink declared input/ancestor: '+name)
        if parent == path:
            if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
                raise ValueError('Nonregular declared input: '+name)
        elif not stat.S_ISDIR(mode):
            raise ValueError('Non-directory declared input ancestor: '+name)
    return path


def declaration(project, component):
    if component not in COMPONENTS:
        raise ValueError('Unknown compile component')
    name = 'containers/native/inputs-'+component+'.json'
    data = json.loads(regular(project, name).read_text())
    required = {'component', 'module', 'templates', 'roots', 'graph', 'external', 'lock_values', 'trees', 'dynamic', 'dynamic_imports'}
    if not isinstance(data, dict) or set(data) != required or data['component'] != component:
        raise ValueError('Missing/unknown component declaration')
    if (any(not isinstance(data[key], list) or any(not isinstance(v, str) for v in data[key])
            for key in ('templates', 'roots', 'external', 'lock_values', 'trees'))
            or not isinstance(data['graph'], dict) or not isinstance(data['dynamic'], dict)
            or not isinstance(data['dynamic_imports'], dict)):
        raise ValueError('Malformed component declarations')
    if any(PurePosixPath(t).name != t or not t.endswith('.py.in') for t in data['templates']):
        raise ValueError('Unsafe component template declaration')
    if data['module'] != 'producer.compile_'+component:
        raise ValueError('Unknown component module')
    return data


def module_target(module, source, project, *, strict=True):
    """Repository imports, including bare native/rendered-template imports."""
    if module.startswith('producer.'):
        return module.replace('.', '/')+'.py'
    if module == 'producer':
        return 'producer/__init__.py'
    if module.startswith('native.'):
        return 'containers/'+module.replace('.', '/')+'.py'
    if module == 'resources' and source == 'containers/native/library_worker.py':
        return 'producer/resources.py'  # copied under this name into the bounded library context
    if module == 'native':
        return None  # namespace package; concrete imported members are checked
    parent = str(PurePosixPath(source).parent)
    for suffix in ('.py', '.py.in', '/__init__.py'):
        target = parent+'/'+module.replace('.', '/')+suffix
        if input_path(project, target).exists():
            return target
    # Missing known local modules must also fail closed.
    if module.startswith(('extra_', 'security_')):
        return parent+'/'+module+'.py'+('.in' if module.startswith('extra_') else '')
    if strict and module.split('.')[0] not in sys.stdlib_module_names:
        raise ValueError('Unresolved Python import requires a declared physical route: '+module)
    return None


def literal_path(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str): return node.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'Path' and len(node.args) == 1:
        return literal_path(node.args[0])
    if isinstance(node, ast.BinOp):
        left, right = literal_path(node.left), literal_path(node.right)
        if left is not None and right is not None:
            if isinstance(node.op, ast.Div): return left.rstrip('/')+'/'+right
            if isinstance(node.op, ast.Add): return left+right
    return None


@lru_cache(maxsize=256)
def syntax(text):
    # Cache only parsing by full content, never dependency resolution or hashes.
    tree = ast.parse(text)
    nodes = tuple(ast.walk(tree))
    strings = tuple(value for n in nodes if (value := literal_path(n)) is not None)
    return nodes, strings


def python_recipe(name, path):
    """Recognize executable Python inputs independently of their filename suffix."""
    if name.endswith(('.py', '.py.in')):
        return True
    with path.open('rb') as stream:
        first = stream.readline(256)
    return first.startswith(b'#!') and re.search(rb'(?:/|\s)python[0-9.]*(?:\s|$)', first) is not None


def references(project, name, *, architecture="amd64", component=None):
    """Derive imports (at every AST depth), literal paths and literal globs."""
    path = regular(project, name)
    python = python_recipe(name, path)
    if not python and not name.endswith('.sh'):
        return set()
    text = path.read_text()
    result = set()
    if python:
        try:
            nodes, strings = syntax(text.replace('@ARCH@', architecture).replace('@MULTIARCH@',
                'x86_64-linux-gnu' if architecture == 'amd64' else 'aarch64-linux-gnu'))
        except SyntaxError as error:
            raise ValueError('Invalid Python recipe: '+name) from error
        for node in nodes:
            modules = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                if node.level:
                    package = PurePosixPath(name).parent.parts
                    package = package[:len(package)-node.level+1]
                    # native is a namespace in the container, not containers.native
                    prefix = '.'.join(package)
                    if prefix == 'containers.native': prefix = 'native'
                    module = prefix+('.'+module if module else '')
                modules = [module]
                for alias in node.names:
                    if alias.name != '*':
                        candidate = module+'.'+alias.name
                        target = module_target(candidate, name, project, strict=False)
                        if target and (module in {'producer', 'native'} or input_path(project, target).exists()): modules.append(candidate)
            elif (isinstance(node, ast.Call) and ((isinstance(node.func, ast.Name) and node.func.id == '__import__')
                    or (isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module'))):
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    modules = [node.args[0].value]
                elif (node.args and isinstance(node.args[0], ast.Subscript)
                      and isinstance(node.args[0].value, ast.Name)
                      and isinstance(node.args[0].slice, ast.Name)
                      and node.args[0].slice.id == 'stage'):
                    # Component-selected literal dispatch tables are executable
                    # routes, not opaque exemptions. Resolve the selected target
                    # independently and require its physical graph edge below.
                    table_name = node.args[0].value.id
                    tables = [n.value for n in nodes if isinstance(n, ast.Assign)
                              and any(isinstance(t, ast.Name) and t.id == table_name for t in n.targets)]
                    try:
                        if len(tables) != 1: raise ValueError('Ambiguous selected route table')
                        table = ast.literal_eval(tables[0])
                        if (not isinstance(table, dict) or
                                any(not isinstance(k, str) or not isinstance(v, str) for k, v in table.items())):
                            raise ValueError('Nonliteral selected route table')
                        modules = [table[component]]
                    except (ValueError, TypeError, KeyError) as error:
                        raise ValueError('Unresolved selected dynamic route: '+name) from error
            for module in modules:
                target = module_target(module, name, project)
                if target: result.add(target)
    else:
        strings = [text]
    # Absolute repository aliases embedded in shell strings are inputs too.
    aliases = ((r'/src/native/', 'containers/native/'), (r'/src/producer/', 'producer/'),
               (r'/src/vyos-patches/', 'patches/vyos/'), (r'/kernel-patches/', 'patches/kernel/'),
               (r'/opt/stream/', 'containers/native/'), (r'/src/', ''))
    for value in strings:
        for alias, canonical in aliases:
            for match in re.finditer(r'(?<![A-Za-z0-9_./-])'+re.escape(alias)+r'([A-Za-z0-9_.*/?+\[\]-]+)', value):
                tail = match[1].rstrip('/')
                if not canonical and (tail in {'native', 'producer', 'vyos-patches'} or tail.startswith(('native/', 'producer/', 'vyos-patches/'))):
                    continue
                target = canonical+tail
                if canonical == 'containers/native/' and target.endswith('.py') and input_path(project, target+'.in').exists():
                    target += '.in'
                if any(c in target for c in '*?['):
                    matches = sorted(input_path(project, canonical).glob(tail))
                    if not matches: raise ValueError('Missing literal recipe glob: '+target)
                    for matched in matches:
                        member = canonical+str(matched.relative_to(input_path(project, canonical)))
                        regular(project, member, directory=matched.is_dir())
                        if not matched.is_dir(): result.add(member)
                elif input_path(project, target).is_dir():
                    regular(project, target, directory=True)
                    # Directory roots alone do not imply consumption of every member.
                    # Literal globs and copytree sources are resolved separately.
                else:
                    result.add(target)
    # Path('/src/native').glob('*.py.in') and similar constant path joins.
    if python:
        for node in nodes:
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {'glob', 'rglob'} and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                root = literal_path(node.func.value)
                for alias, canonical in aliases:
                    if root == alias.rstrip('/'):
                        pattern = node.args[0].value
                        matches = getattr(input_path(project, canonical), node.func.attr)(pattern)
                        found = []
                        for matched in matches:
                            member = canonical+str(matched.relative_to(input_path(project, canonical)))
                            regular(project, member, directory=matched.is_dir())
                            if not matched.is_dir(): found.append(member)
                        if not found: raise ValueError('Missing literal recipe glob')
                        result.update(found)
    if python:
        for node in nodes:
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'copytree' and node.args):
                source = literal_path(node.args[0])
                for alias, canonical in aliases:
                    if source and source.startswith(alias):
                        member = canonical+source.removeprefix(alias).rstrip('/')
                        if not canonical and member.startswith(('native/', 'producer/', 'vyos-patches/')): continue
                        root = regular(project, member, directory=True)
                        for entry in sorted(root.rglob('*')):
                            target = member+'/'+str(entry.relative_to(root))
                            regular(project, target, directory=entry.is_dir())
                            if not entry.is_dir(): result.add(target)
                        break
    result.discard(name)
    return result


@lru_cache(maxsize=256)
def lock_selections(text):
    """Check literal reads from the two structurally selected lock documents.

    Computed keys still require explicit selections; this is a local syntax
    analysis, not a general Python data-flow proof.
    """
    tree = ast.parse(text)
    documents = {'source-lock.json', 'external-sources.lock.json'}
    selections, git_calls = set(), set()
    def origin(node, variables):
        if isinstance(node, ast.Name): return variables.get(node.id)
        if isinstance(node, ast.Subscript):
            base = origin(node.value, variables)
            if base and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                return base[0], '.'.join(filter(None, (base[1], node.slice.value)))
            return None
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'loads' and node.args):
            read = node.args[0]
            if isinstance(read, ast.Call) and isinstance(read.func, ast.Attribute) and read.func.attr == 'read_text':
                # Literal filename, including project/'source-lock.json' and regular(...).
                names = {n.value.rsplit('/', 1)[-1] for n in ast.walk(read.func.value)
                         if isinstance(n, ast.Constant) and isinstance(n.value, str)}
                selected = names & documents
                if len(selected) == 1: return next(iter(selected)), ''
        return None
    scopes = [tree, *(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))]
    for scope in scopes:
        variables = {}
        # Walk each function separately so identical local variable names cannot
        # accidentally inherit another function's lock identity.
        pending = list(scope.body)
        while pending:
            node = pending.pop(0)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): continue
            if isinstance(node, ast.Assign):
                value = origin(node.value, variables)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if value: variables[target.id] = value
                        else: variables.pop(target.id, None)
            if isinstance(node, ast.Subscript):
                value = origin(node, variables)
                if value and value[1]: selections.add(value)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'git'
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                    ):
                git_calls.add(node.args[0].value)
            pending[0:0] = list(ast.iter_child_nodes(node))

    return tuple(sorted(selections)), tuple(sorted(git_calls))


def lock_references(project, name, data):
    if not python_recipe(name, regular(project, name)): return
    allowed = {'source-lock.json': ('stream', 'container_bases', 'debian_snapshot',
               'debian_security_snapshot', *data['lock_values']),
               'external-sources.lock.json': tuple(data['external'])}
    selections, git_calls = lock_selections(regular(project, name).read_text())
    for document, value in selections:
        if not any(value == key or value.startswith(key+'.') or key.startswith(value+'.')
                   for key in allowed[document]):
            raise ValueError('Undeclared lock selection in '+name+': '+document+':'+value)
    if not set(git_calls) <= set(data['external']):
        raise ValueError('Undeclared external Git selection in '+name)


def graph(project, component):
    data = declaration(project, component)
    common_name = 'containers/native/inputs-common.json'
    common = json.loads(regular(project, common_name).read_text())
    if (not isinstance(common, dict) or set(common) != {'roots', 'graph', 'dynamic', 'dynamic_imports'}
            or not isinstance(common['roots'], list) or not isinstance(common['graph'], dict)
            or not isinstance(common['dynamic'], dict) or not isinstance(common['dynamic_imports'], dict)
            or any(not isinstance(root, str) for root in common['roots'])):
        raise ValueError('Invalid common graph declaration')
    nodes = dict(common['graph'])
    if set(nodes) & set(data['graph']):
        raise ValueError('Duplicate dependency declaration')
    nodes.update(data['graph'])
    roots = common['roots']+data['roots']
    for prefix in data['trees']:
        root = regular(project, prefix, directory=True)
        for path in sorted(root.rglob('*')):
            name = prefix+'/'+str(path.relative_to(root))
            if path.is_dir():
                regular(project, name, directory=True)
            else:
                regular(project, name)
                nodes[name] = []
                roots.append(name)
    dynamic = {**common['dynamic'], **data['dynamic']}
    dynamic_imports = {**common['dynamic_imports'], **data['dynamic_imports']}
    for name, routes in dynamic_imports.items():
        if (name not in nodes or not isinstance(routes, list)
                or any(not isinstance(route, str) for route in routes) or len(routes) != len(set(routes))):
            raise ValueError('Invalid dynamic import declaration')
    for name, edges in nodes.items():
        if (not isinstance(name, str) or not isinstance(edges, list)
                or any(not isinstance(dep, str) for dep in edges) or len(edges) != len(set(edges))):
            raise ValueError('Malformed graph node or edges')
    for name, edges in dynamic.items():
        if name not in nodes or not isinstance(edges, list) or any(not isinstance(dep, str) or dep not in nodes for dep in edges):
            raise ValueError('Unknown dynamic dependency route')
        nodes[name] = sorted(set(nodes[name]) | set(edges))
    for name, edges in nodes.items():
        regular(project, name)
        if (not isinstance(edges, list) or any(not isinstance(v, str) for v in edges)
                or len(edges) != len(set(edges))):
            raise ValueError('Invalid dependency edges: '+name)
        for dep in edges:
            if dep not in nodes: raise ValueError('Unknown dependency: '+dep)
        if python_recipe(name, regular(project, name)):
            rendered = regular(project, name).read_text().replace('@ARCH@', 'amd64').replace('@MULTIARCH@', 'x86_64-linux-gnu')
            calls, _ = syntax(rendered)
            routes = set()
            for call in calls:
                if (isinstance(call, ast.Call) and
                        ((isinstance(call.func, ast.Name) and call.func.id == '__import__') or
                         (isinstance(call.func, ast.Attribute) and call.func.attr == 'import_module')) and
                        (not call.args or literal_path(call.args[0]) is None)):
                    routes.add(ast.unparse(call.args[0]) if call.args else '<missing argument>')
            if routes != set(dynamic_imports.get(name, ())):
                raise ValueError('Missing/extra dynamic import route declaration: '+name)
        lock_references(project, name, data)
        detected = references(project, name, component=component)
        if name.endswith('.py.in'):
            detected |= references(project, name, architecture='arm64', component=component)
        tree_targets = {dep for dep in detected if any(dep.startswith(t+'/') for t in data['trees'])}
        edges.extend(sorted(tree_targets-set(edges)))
        if not detected <= set(edges):
            raise ValueError('Undeclared dependency in '+name+': '+', '.join(sorted(detected-set(edges))))
    for name, edges in dynamic.items():
        if name not in nodes or not set(edges) <= set(nodes[name]):
            raise ValueError('Missing declared dynamic route: '+name)
    visited = set()
    def visit(name):
        if name not in nodes: raise ValueError('Unknown dependency root: '+name)
        if name in visited: return
        visited.add(name)
        for dep in nodes[name]: visit(dep)
    for name in roots: visit(name)
    if visited != set(nodes): raise ValueError('Unreachable dependency declarations')
    if data['module'].replace('.', '/')+'.py' not in visited:
        raise ValueError('Missing declared component recipe')
    if {name for name in visited if name.endswith('.py.in')} != {'containers/native/'+t for t in data['templates']}:
        raise ValueError('Missing rendered-template declaration')
    for template in data['templates']:
        if 'containers/native/'+template not in visited:
            raise ValueError('Undeclared rendered template')
    return data, nodes
