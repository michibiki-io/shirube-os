"""Native compilation tranches and explicit key-free source/SDK exports.

These are compiler entry points, not release clearance. No raw working tree is
exported: source selectors reuse the corresponding-source inventory contracts.
"""
import json
from pathlib import Path
import shutil
import subprocess

from .source_selectors import files
from .sources import sha256

COMPILE_STAGES = ('native', 'vpp', 'kea', 'salt', 'runtime', 'kernel')

DEPENDENCIES = {name: (('vpp',) if name == 'kernel' else ()) for name in COMPILE_STAGES}

# Explicit source mappings; snapshot inventories supply the original member set.




def select_jobs(catalog, stage):
    if stage not in COMPILE_STAGES: raise ValueError('Unknown compile stage')
    return [job for job in catalog if (job['name'] == 'kernel') == (stage == 'kernel')] if stage in {'native', 'kernel'} else []


def copy_selected(selected, destination):
    """Materialize only selected regular preferred sources, not directory aliases."""
    destination = Path(destination); destination.mkdir(parents=True, exist_ok=True)
    inventory = []
    for source, member in selected:
        from .stage_artifacts import member_name
        member_name(member)
        target = destination / member
        if target.exists():
            if sha256(target) != sha256(source): raise ValueError('Conflicting source selection')
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o755 if source.stat().st_mode & 0o111 else 0o644)
        inventory.append(member)
    if not inventory: raise ValueError('Empty preferred source selection')
    return inventory


def snapshot_files(actual, preferred, *, rename_pam=False):
    """Retain actual patched source bytes only at authenticated original paths.

    Generated dependency inventories and exact build identity are explicit additions;
    package/, build/, virtualenvs and arbitrary new compiler outputs are excluded.
    """
    actual, preferred = Path(actual), Path(preferred)
    for _, member in files(preferred, exclude=('build', 'debian/.debhelper')):
        if rename_pam and member.startswith('debian/libpam-radius-auth.'):
            member=member.replace('debian/libpam-radius-auth.','debian/vyos-libpam-radius-auth.',1)
        path = actual / member
        if path.is_file():
            if not path.resolve().is_relative_to(actual.resolve()): raise ValueError('External source alias')
            yield path.resolve(), member
    for path, member in files(actual / 'shirube-corresponding-inputs'):
        yield path, 'shirube-corresponding-inputs/' + member
    if (actual / 'SHIRUBE-BUILD.json').is_file(): yield actual / 'SHIRUBE-BUILD.json', 'SHIRUBE-BUILD.json'




def component(stage):
    from .component_graph import declaration
    import importlib
    return importlib.import_module(declaration(Path('/src'), stage)['module'])


def export_compile(stage, sources, packages, destination, architecture):
    """Export an explicit key-free material layout consumed by collect()."""
    from .stage_artifacts import inventory
    destination = Path(destination)
    material = destination / 'material'
    catalog = json.loads(Path('/src/native/packages.json').read_text())['jobs']
    for job in select_jobs(catalog, stage):
        copy_selected(files(sources / job['source'], exclude=(
            'debian', 'include/generated', 'arch/arm64/include/generated', 'arch/x86/include/generated',
            'certs/signing_key.x509', 'arch/arm64/boot/Image', 'arch/arm64/boot/Image.gz',
            'arch/x86/boot/bzImage', 'vmlinux.btf', 'Module.symvers', 'modules.order',
            'modules.builtin', 'modules.builtin.modinfo') if job['name'] == 'kernel' else ()),
            material / 'sources' / job['source'])
        if job['name'] == 'vyos-http-api-tools':
            copy_selected(files(Path('/build-private') / job['name'] / 'source'),
                          material / 'native' / job['name'])
        for path in (packages / job['name']).iterdir():
            if path.is_file(): copy_selected([(path, path.name)], destination / 'packages' / job['name'])
    # The builder recipes are authenticated original inputs, not compiled trees.
    copy_selected(files(sources / 'vyos-build/vyos-build'), material / 'sources/vyos-build/vyos-build')
    component(stage).export_material(stage, sources, packages, destination, architecture)
    owned_elsewhere = set()
    for predecessor in DEPENDENCIES[stage]:
        owned_elsewhere.update(p.name for p in (Path('/stage-dependencies')/predecessor/'debs').glob('*.deb'))
    for path in Path('/debs').glob('*.deb'):
        if path.name in owned_elsewhere and not any((packages/job['name']/path.name).exists()
                for job in select_jobs(catalog, stage)):
            continue
        copy_selected([(path, path.name)], destination / 'debs')
    inventory(destination)


def finish_compile(stage, sources, packages, public_output, architecture):
    """All signing happens here, before the only export, in one private job."""
    from .native_common import generate, run
    recipe = component(stage)
    extras = recipe.EXTRAS[stage]
    root = generate(architecture, stage=stage)
    Path('/sources').symlink_to(sources)
    debs = Path('/debs'); debs.mkdir(exist_ok=True)
    for package in packages.rglob('*.deb'): shutil.copy2(package, debs / package.name)
    if hasattr(recipe, 'prepare_runtime'):
        recipe.prepare_runtime(debs)
    if extras:
        run('apt-get', '-o', 'APT::Update::Error-Mode=any', 'update')
    for job in extras:
        print('STAGE: native runtime ' + job, flush=True)
        run('python3', str(root / ('extra_'+stage+'.py')), job)
    from .package_notices import COMPONENTS, retain
    for package in debs.glob('*.deb'):
        name=subprocess.check_output(['dpkg-deb','-f',str(package),'Package'],text=True).strip()
        if name in COMPONENTS:
            retain(package,COMPONENTS[name][1],Path('/build-private')/('notices-'+name))
    from .packages import audit
    certificate = recipe.certificate() if hasattr(recipe, 'certificate') else None
    for package in debs.glob('*.deb'):
        audit(package, architecture, certificate)
    export_compile(stage, sources, packages, public_output, architecture)
    from .library_cache import compiler_fingerprint
    observations=Path(public_output)/'provenance';observations.mkdir(exist_ok=True)
    shutil.copyfile(packages/'source-preparation.json',observations/(stage+'-source-preparation.json'))
    (observations/(stage+'-compiler.json')).write_text(json.dumps({
        'post_build_compiler_fingerprint':compiler_fingerprint(),
        'scope':'Actual installed compiler environment after this pinned stage recipe'},sort_keys=True)+'\n')


def restore_ocaml_evidence(material, destination, work):
    """Rebind producer source references, preserving the exact archived bytes."""
    material, destination, work = map(Path, (material, destination, work))
    receipt = json.loads((material / 'receipt.json').read_text())
    shutil.copyfile(material / 'sources.tar.gz', destination)
    work.mkdir()
    def rebind(value):
        if isinstance(value, list): return [rebind(v) for v in value]
        if isinstance(value, dict):
            result = {k: rebind(v) for k, v in value.items()}
            if result.get('asset') == 'sources.tar.gz': result['asset'] = destination.name
            if 'notice' in result:
                name = Path(result['notice']).name
                target = work / name
                shutil.copyfile(material / 'notices' / name, target)
                result['notice'] = target
            return result
        return value
    return rebind(receipt['records']), rebind(receipt['components'])
