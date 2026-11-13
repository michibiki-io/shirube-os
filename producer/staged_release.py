"""Downstream integration, ISO, clearance and guest execution contracts."""
import argparse
import json
import os
import tempfile
from pathlib import Path
import shutil
import sys
import time

from .native_stages import COMPILE_STAGES, copy_selected
from .stage_artifacts import attribution, pack, scope
from .sources import sha256

DEPENDENCIES = {s: () for s in COMPILE_STAGES}
DEPENDENCIES.update(kernel=('vpp',), integration=('native', 'vpp'),
                    iso=(*COMPILE_STAGES, 'integration'),
                    clearance=('iso',), guests=('clearance',))
STAGES = tuple(DEPENDENCIES)


def validate_discard_inputs(stage, directory, output, cache=None, *, protected_paths=()):
    """Writable inputs must be a dedicated task directory, separate from outputs."""
    if stage not in {'iso', 'clearance', 'guests'}:
        raise ValueError('--discard-input-archives requires ISO, clearance or guests')
    directory = Path(directory)
    resolved = directory.resolve()
    protected = [Path(os.getcwd()), Path(os.path.expanduser('~')),
                 Path(tempfile.gettempdir()), Path('/var/tmp')]
    if (directory.absolute() != resolved
            or any(resolved == p or resolved in p.parents for p in protected)):
        raise ValueError('Dedicated non-symlink task input directory required')
    for other in (output, cache, '/src', '/build-private', '/stage-dependencies', *protected_paths):
        if other is None:
            continue
        other = Path(other).resolve()
        if resolved == other or resolved in other.parents or other in resolved.parents:
            raise ValueError('Task inputs must not overlap output, cache or build paths')


def build_scope(project, stage, architecture, compiler, dependencies):
    """Canonical compile manifest or broad downstream inputs; separate attribution."""
    if stage not in DEPENDENCIES: raise ValueError('Unknown build stage')
    expected = set(DEPENDENCIES[stage])
    if set(dependencies) != expected: raise ValueError('Incomplete stage predecessors')
    if stage in COMPILE_STAGES:
        from .compile_scope import build_scope as compile_scope
        return compile_scope(project, stage, architecture, compiler, dependencies)
    project = Path(project)
    native = project / 'containers/native'
    if not native.is_dir(): native = project / 'native'
    paths = [project / 'source-lock.json']
    for pattern in ('*.json','*.py','*.sh','*.in'):
        paths += list(native.rglob(pattern))
    paths += [project/name for name in ('LICENSE','shirube-os-logo.svg')]
    if stage not in COMPILE_STAGES:
        for name in ('guest','LICENSES','third_party/geoip'):
            paths += [p for p in (project/name).rglob('*') if p.is_file()]
        paths += [project/name for name in ('NOTICE','license-audit.json')]
    paths += [native / name for name in ('Dockerfile', 'build.py', 'package.sh', 'extras.py.in', 'image.py')]
    # All producer code is relevant to source/security export contracts. This
    # deliberately invalidates on contract changes, but not on docs/commit IDs.
    paths += list((project / 'producer').rglob('*.py'))
    for directory in (project / 'patches', project / 'vyos-patches', project / 'nmap-patches', Path('/kernel-patches')):
        if directory.is_dir(): paths += [p for p in directory.rglob('*') if p.is_file()]
    inputs = {str(p.relative_to(project)) if p.is_relative_to(project) else 'kernel-patches/'+p.name: sha256(p)
              for p in paths}
    return scope(stage, architecture, inputs, compiler, dependencies)


from .stage_transport import merge_tree, restore_inputs as restore_transfers


def restore_inputs(*args):
    return restore_transfers(*args, dependencies=DEPENDENCIES, build_scope=build_scope)


def merged_compile_material(payloads, destination):
    destination = Path(destination); destination.mkdir()
    for stage, payload in payloads.items():
        if stage == 'integration': continue
        merge_tree(payload, destination)
    if 'integration' in payloads:
        # Integration replaces the native core with its branded version.
        for path in (destination / 'debs').glob('vyos-1x_*.deb'): path.unlink()
        merge_tree(payloads['integration'], destination)
    return destination


def link_material(material):
    """Legacy recipe roots point only at verified, inventoried key-free inputs."""
    for name in ('sources', 'extra'):
        source = Path(material) / name
        if source.is_dir(): Path('/'+name).symlink_to(source)


def integration(payloads, output, architecture, version):
    from .native_stages import files
    material = merged_compile_material(payloads, Path('/build-private/integration-input'))
    link_material(material / 'material')
    shutil.copytree(material / 'debs', '/debs')
    sys.path.insert(0, '/src/native')
    from image import generate, guest_package
    generate(architecture)
    from .cloud_init import build
    build(Path('/src'), Path('/guest-cloud-init'), Path('/debs'))
    guest_package(architecture, version)
    for pattern in ('shirube-integration_*.deb', 'cloud-init_*.deb', 'vyos-1x_*.deb'):
        found = list(Path('/debs').glob(pattern))
        if len(found) != 1: raise ValueError('Integration output missing/ambiguous')
        copy_selected([(found[0], found[0].name)], output / 'debs')
    for name in ('netbird-correspondence', 'vpp-input-sources'):
        copy_selected(files(Path('/guest-package') / name), output / 'material/guest-package' / name)
    copy_selected([(Path('/guest-cloud-init/upstream.tar.gz'), 'upstream.tar.gz')], output / 'material/guest-cloud-init')
    lock = json.loads(Path('/src/source-lock.json').read_text())['cloud_init']
    root = Path('/guest-cloud-init/original') / ('vyos-cloud-init-'+lock['commit'])
    names = ['LICENSE', 'LICENSE-GPLv3', 'LICENSE-Apache2.0', 'packages/debian/copyright',
             'cloudinit/config/cc_vyos.py', 'cloudinit/config/cc_vyos_userdata.py']
    copy_selected([(root / name, name) for name in names],
                  output / 'material/guest-cloud-init/original' / root.name)


def iso(payloads, output, architecture, version):
    sys.path.insert(0, '/src/native')
    from image import assemble
    merged = merged_compile_material(payloads, Path('/build-private/iso-input'))
    link_material(merged / 'material')
    result = assemble(merged / 'material/sources', merged / 'debs', architecture, version, output, prepared=merged)
    # Carry only key-free, explicitly inventoried source/build material for
    # clearance. No ISO assembly rootfs, repository keys or compiler work trees.
    for name in ('material', 'packages', 'ocaml', 'signing'):
        source = merged / name
        if source.is_dir(): merge_tree(source, output / name)
    # Assembly can repair repository metadata (mapuser compatibility). Carry
    # the exact DEBs consumed by live-build, not their pre-assembly versions.
    merge_tree(Path('/debs'), output / 'debs')
    import secrets
    result['generation']=secrets.token_hex(32)
    (output / 'assembly.json').write_text(json.dumps({key: (value.name if key == 'iso' else value)
        for key, value in result.items() if key not in {'source', 'sources'}}, sort_keys=True) + '\n')


def clearance(payloads, output, architecture, version, commit, *, diagnostic=None):
    from .pipeline import collect_iso_sources, source_set_evidence, verify_branding
    payload = payloads['iso']
    result = json.loads((payload / 'assembly.json').read_text())
    result['iso'] = payload / result['iso']
    if sha256(result['iso']) != json.loads((payload / 'iso-binding.json').read_text())['sha256']:
        raise ValueError('Actual ISO hash differs from assembly')
    if sha256(payload/'signing/cert.der')!=result['kernel_certificate_sha256']:
        raise ValueError('Assembly public signing certificate differs')
    shutil.copyfile(result['iso'], output / result['iso'].name)
    # collect compares the complete actual ISO against these producer DEBs.
    Path('/debs').symlink_to(payload / 'debs')
    rootfs, source = collect_iso_sources(result['iso'], payload / 'packages', output, architecture, version,
                                       material=payload / 'material')
    reconstruction = source_set_evidence(source, output, architecture, version, commit,
                                        Path('/build-private/source-set-restoration'))
    if diagnostic is not None:
        Path(diagnostic).write_text(json.dumps({**attribution(commit,version),'architecture':architecture,
            'iso_sha256':sha256(result['iso']),'source_inventory_sha256':sha256(source['inventory']),
            'source_restoration':reconstruction['proof'],
            'license_clearance':'PASS' if source['cleared'] is True else 'FAIL',
            'release_clearance':'NOT_CLEARED','oci':'NOT_RUN'},sort_keys=True)+'\n')
    if source['cleared'] is not True: raise ValueError('Unresolved component redistribution permission')
    branding_verified=verify_branding(rootfs)
    if not branding_verified:raise ValueError('Actual ISO branding failed')
    record = {'assembly': {**result, 'iso': result['iso'].name}, 'iso_sha256': sha256(result['iso']),
              **attribution(commit, version), 'architecture': architecture,
              'branding_verified': branding_verified, 'source': source,
              'reconstruction': reconstruction}
    kernels=list((payload/'debs').glob('linux-image-*_'+architecture+'.deb'))
    if len(kernels)!=1:raise ValueError('One task-built guest appliance kernel required')
    copy_selected([(kernels[0],kernels[0].name),(payload/'signing/cert.der','cert.der')],output/'guest-kernel')
    # Paths are logical asset names; downstream never trusts compiler paths.
    record['source'] = {**source, 'inventory': source['inventory'].name,
                        'archives': [p.name for p in source['archives']]}
    (output / 'clearance.json').write_text(json.dumps(record, default=str, sort_keys=True)+'\n')


def verify_clearance(payload, record, architecture, version, commit, project=Path('/project-source.tar')):
    """Consume hash-bound clearance without repeating collection/license review."""
    from .release import filename
    if (any(record.get(k)!=v for k,v in attribution(commit,version).items())
            or record.get('architecture')!=architecture or record.get('branding_verified') is not True
            or record['source'].get('cleared') is not True):
        raise ValueError('Clearance attribution or admission differs')
    result=record['assembly']
    if result['iso']!=f'shirube-os-{version}-{architecture}.iso':
        raise ValueError('Cleared ISO identity differs')
    if sha256(payload/filename(result['iso']))!=record['iso_sha256']:
        raise ValueError('Cleared ISO changed')
    reconstruction=record['reconstruction'];proof=reconstruction['proof']
    header=reconstruction['input_manifest']
    if (proof.get('status')!='PASS_SOURCE_SET_ONLY' or proof.get('source_commit')!=commit
            or proof.get('architecture')!=architecture
            or proof.get('actual_project_build_input_binding')!='PASS'
            or proof.get('actual_project_build_input_sha256')!=sha256(project)
            or header.get('source_commit')!=commit or header.get('version')!=version
            or header.get('source_license_clearance')!='PASS'
            or header.get('manifest_kind')!='candidate-source-set'):
        raise ValueError('Cleared source proof differs from current recipe/commit')
    manifest=json.loads((payload/'manifest.json').read_text())
    if (manifest.get('source_set_reconstruction')!=reconstruction
            or {k:v for k,v in manifest.items() if k!='source_set_reconstruction'}!=header):
        raise ValueError('Source restoration receipt differs')
    names=[record['source']['inventory'],*record['source']['archives']]
    assets=header['assets']
    if (len(names)!=len(set(names)) or len(assets)!=len(names)
            or {a['name'] for a in assets}!=set(names)):
        raise ValueError('Cleared source inventory differs')
    for asset in assets:
        path=payload/filename(asset['name'])
        role='licenses' if asset['name']==record['source']['inventory'] else 'source'
        if (asset['architecture']!=architecture or asset['role']!=role
                or path.stat().st_size!=asset['size'] or sha256(path)!=asset['sha256']):
            raise ValueError('Cleared source bytes differ')


def guests(payloads, output, architecture, version, commit, management, accelerator):
    from .pipeline import complete_candidate
    payload = payloads['clearance']
    record = json.loads((payload / 'clearance.json').read_text())
    verify_clearance(payload,record,architecture,version,commit)
    certificate=payload/'guest-kernel/cert.der'
    if sha256(certificate)!=record['assembly']['kernel_certificate_sha256']:
        raise ValueError('Cleared guest kernel certificate differs')
    from .packages import audit
    kernels=list((payload/'guest-kernel').glob('linux-image-*_'+architecture+'.deb'))
    if len(kernels)!=1:raise ValueError('One cleared guest appliance kernel required')
    audit(kernels[0],architecture,certificate)
    Path('/debs').symlink_to(payload/'guest-kernel')
    for item in payload.iterdir():
        if item.is_file():shutil.move(item,output/item.name)
    (output / 'manifest.json').unlink()  # Candidate source-set metadata, not release clearance.
    result = record['assembly']; result['iso'] = output / result['iso']
    if sha256(result['iso']) != record['iso_sha256']: raise ValueError('Cleared ISO changed')
    source = record['source']; source['inventory'] = output / source['inventory']
    source['archives'] = [output / p for p in source['archives']]
    # The release validator expects only actual release assets at this point.
    (output / 'clearance.json').unlink()
    complete_candidate(result, source, record['reconstruction'], output, architecture, version, commit, management,
                       qemu_accelerator=accelerator, branding_verified=record['branding_verified'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=tuple(s for s in STAGES if s not in COMPILE_STAGES))
    parser.add_argument('architecture', choices=('amd64', 'arm64'))
    parser.add_argument('--commit', required=True); parser.add_argument('--version', required=True)
    parser.add_argument('--inputs', type=Path, default=Path('/inputs'))
    parser.add_argument('--output', type=Path, default=Path('/output'))
    parser.add_argument('--qemu-accelerator', choices=('kvm','tcg'), default='tcg')
    parser.add_argument('--netbird-fixture-stdin', action='store_true')
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--discard-input-archives', action='store_true',
                        help='ISO, clearance and guests only: retire task-owned input tars after verification and metrics')
    args = parser.parse_args()
    if args.discard_input_archives:
        validate_discard_inputs(args.stage, args.inputs, args.output, args.cache)
    from .architecture import native
    native(args.architecture); attribution(args.commit, args.version)
    from .policy import require_stream_version
    require_stream_version(args.version, json.loads(Path('/src/source-lock.json').read_text())['stream']['version'])
    management = None
    if args.stage == 'guests':
        if not args.netbird_fixture_stdin: raise ValueError('Guest fixture required')
        from .netbird_guest import Management
        management = Management(json.load(sys.stdin))
        import os
        descriptor = os.open(os.devnull, os.O_RDONLY); os.dup2(descriptor, 0); os.close(descriptor)
    elif args.netbird_fixture_stdin: raise ValueError('Compiler stages cannot consume fixture credentials')
    if any(args.output.iterdir()): raise ValueError('Fresh stage output required')
    work = Path('/build-private'); work.mkdir(mode=0o700)
    from .library_cache import compiler_fingerprint
    compiler = compiler_fingerprint()
    from .stage_metrics import measure
    details={**attribution(args.commit,args.version),'cache':'DISABLED','transfer':'NOT_RECORDED'}
    with measure(args.stage, args.architecture, args.output / 'resources.json', work,details=details):
        transfer_started=time.monotonic()
        dependencies, payloads = restore_inputs(args.inputs, Path('/stage-dependencies'), args.stage,
                                                args.architecture, args.commit, args.version, compiler)
        details['incoming_verify_unpack_seconds']=round(time.monotonic()-transfer_started,3)
        details['inputs']=[{'stage':stage,**{k:json.loads((args.inputs/(stage+'.json')).read_text())[k]
                                          for k in ('sha256','bytes')}} for stage in payloads]
        expected = build_scope(Path('/src'), args.stage, args.architecture, compiler, dependencies)
        details['input_id']=expected['input_id'];details['compiler_fingerprint']=compiler
        if args.discard_input_archives:
            # restore_inputs validated the entire cohort, including ancestry;
            # hashes/sizes above remain available after retiring only its tars.
            for dependency in DEPENDENCIES[args.stage]:
                (args.inputs / (dependency+'.tar')).unlink()
        if args.stage not in COMPILE_STAGES and args.stage!='guests':
            from .architecture import require_storage,BUILD_STORAGE_GIB
            require_storage(work,BUILD_STORAGE_GIB,phase='Staged source/ISO workspace')
        payload = work / 'payload'
        payload.mkdir()
        if args.stage == 'integration': integration(payloads, payload, args.architecture, args.version)
        elif args.stage == 'iso':
            iso(payloads, payload, args.architecture, args.version)
            result = json.loads((payload / 'assembly.json').read_text())
            (payload / 'iso-binding.json').write_text(json.dumps({'sha256': sha256(payload/result['iso'])})+'\n')
        elif args.stage == 'clearance':
            clearance(payloads, payload, args.architecture, args.version, args.commit,
                      diagnostic=args.output/'clearance-diagnostic.json')
        else: guests(payloads, payload, args.architecture, args.version, args.commit, management, args.qemu_accelerator)
        if args.stage == 'guests':
            details['transfer']={'candidate_bytes':sum(p.stat().st_size for p in payload.iterdir())}
            # These release assets pass the existing complete manifest validator.
            # They are not a compiler transfer and must not include extra receipts.
            shutil.move(payload, args.output / 'candidate')
            return
        transfer_started=time.monotonic()
        transfer = pack(payload, args.output / 'transfer.tar', expected, args.commit, args.version,
                        content_source_commit=None)
        (args.output / 'transfer.json').write_text(json.dumps(transfer, sort_keys=True)+'\n')
        details['transfer']={'sha256':transfer['sha256'],'bytes':transfer['bytes'],
                             'pack_seconds':round(time.monotonic()-transfer_started,3)}
        # Remove redundant task-owned exports after the verified archive exists.
        if payload.is_relative_to(args.output):
            shutil.rmtree(payload)
