"""Complete candidate assembly; missing source/license/runtime proof fails publication."""
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tarfile
from .release import CHECKS, validate
from .sources import sha256


def source_set_evidence(source, output, architecture, version, commit, work,
                        project=Path('/project-source.tar')):
    """Actually restore all source materials and bind the real build recipe.

    This checks corresponding-source reconstruction, not bit reproducibility
    or a second compilation of unmodified dependency binaries.
    """
    from .reconstruction import restore
    output,work,project=Path(output),Path(work),Path(project)
    if project.is_symlink() or not project.is_file():
        raise ValueError('Exact project build input is missing')
    with tarfile.open(project) as archive:
        if archive.pax_headers.get('comment')!=commit:
            raise ValueError('Project build archive commit differs')
    project_digest=sha256(project)
    inventory=json.loads(Path(source['inventory']).read_text())
    def project_binding(component):
        if not any(r['member']=='project-source.tar' and r['sha256']==project_digest
                   for r in component.get('build_inputs',[])):
            raise ValueError('Component recipes do not bind the actual project build input')
        for child in component.get('bundled_components',[]):project_binding(child)
    for component in inventory['components']:project_binding(component)
    manifest_path=output/'manifest.json'
    if manifest_path.exists() or manifest_path.is_symlink() or work.exists() or work.is_symlink():
        raise ValueError('Fresh candidate source metadata and restoration workspace required')
    assets=[{'name':path.name,'role':role,'architecture':architecture,
             'size':path.stat().st_size,'sha256':sha256(path)}
            for path,role in [(Path(source['inventory']),'licenses')]+
                [(Path(path),'source') for path in source['archives']]]
    header={'schema_version':1,'manifest_kind':'candidate-source-set',
            'project':'Shirube OS','provider':'michibiki-io','source_commit':commit,
            'version':version,'assets':assets,
            'source_license_clearance':'PASS' if source.get('cleared') is True else 'FAIL',
            'release_clearance':'NOT_CLEARED'}
    manifest_path.write_text(json.dumps(header,sort_keys=True,separators=(',',':'))+'\n')
    restored=False
    try:
        proof=restore(output,work,architecture,commit,discard_verified=True);restored=True
        if proof['status']!='PASS_SOURCE_SET_ONLY':raise ValueError('Source-set restoration did not pass')
        proof['actual_project_build_input_sha256']=project_digest
        proof['actual_project_build_input_binding']='PASS'
        result={'scope':'Complete source-set restoration and actual build-recipe binding',
                'input_manifest':header,'proof':proof}
        # Preserve the completed source check even if later license/guest
        # admission fails. This intermediate kind cannot pass publication.
        manifest_path.write_text(json.dumps({**header,'source_set_reconstruction':result},
                                            sort_keys=True,separators=(',',':'))+'\n')
        return result
    finally:
        # Only this successful task-owned restoration is disposable. A failed
        # restore owns its cleanup; an existing directory is never removed here.
        if restored:shutil.rmtree(work)


def collect_iso_sources(iso,packages,output,architecture,version):
    """Audit source/license inputs independently of virtualization availability."""
    from .collect import collect
    rootfs=Path('/build-private/release-rootfs')
    subprocess.run(['xorriso','-osirrox','on','-indev',str(iso),'-extract',
        '/live/filesystem.squashfs','/build-private/iso-root.squashfs'],check=True)
    subprocess.run(['unsquashfs','-d',str(rootfs),'/build-private/iso-root.squashfs'],
        check=True,stdout=subprocess.DEVNULL)
    from .factory_audit import audit_iso
    factory=audit_iso(iso,rootfs,architecture,Path('/build-private/factory-content-audit'))
    source=collect(rootfs,packages,output,architecture,version,Path('/build-private/corresponding-source'))
    source['factory_content_audit']=factory
    return rootfs,source


def release_candidate(sources, packages, output, architecture, version, commit, management,qemu_accelerator='kvm',
                      retire_compiler=None):
    from .architecture import release_accelerator
    qemu_accelerator=release_accelerator(architecture,qemu_accelerator)
    sys.path.insert(0,'/src/native')
    from image import assemble
    from .vm import build_disks
    print('STAGE: native ISO assembly and architecture/signature inspection',flush=True)
    result=assemble(sources,packages,architecture,version,output)
    print('STAGE: exact corresponding source collection and component/license clearance',flush=True)
    rootfs,source=collect_iso_sources(result['iso'],packages,output,architecture,version)
    print('STAGE: complete source-set restoration and exact build-recipe binding',flush=True)
    reconstruction=source_set_evidence(source,output,architecture,version,commit,
                                       Path('/build-private/source-set-restoration'))
    if source['cleared'] is not True:
        print('SOURCE LICENSE CLEARANCE: FAIL; guest tests and publication are not attempted',flush=True)
        raise ValueError('Unresolved component redistribution permission')
    # The exact source archives and recipes have been physically restored and
    # verified. Guest appliances use the retained package-output kernel DEBs;
    # they do not need compiler object files or private module signing keys.
    retired=retire_compiler() if retire_compiler is not None else None
    from .architecture import require_storage, GUEST_STORAGE_GIB
    require_storage(Path('/build-private'), GUEST_STORAGE_GIB, phase='Fresh guest disk stage')
    print('STAGE: fresh install, sanitization, conversions and fresh-instance boot/access tests',flush=True)
    disks=build_disks(result['iso'],architecture,version,Path('/build-private/disk'),output,management.verify,accelerator=qemu_accelerator)
    checks={name:'NOT_RUN' for name in CHECKS}
    for name in ['build','architecture','module_signatures','sanitization','disk_equivalence','local_boot',
                 'unprovisioned_boot','provisioned_access','identity_regeneration']:
        checks[name]='PASS'
    os_release=(rootfs/'etc/os-release').read_text()
    checks['branding']='PASS' if 'NAME="Shirube OS"' in os_release and (rootfs/'usr/share/shirube/logo.svg').read_bytes()==Path('/src/shirube-os-logo.svg').read_bytes() else 'FAIL'
    checks['licenses']='PASS' if source['cleared'] else 'FAIL'
    checks['corresponding_source']='PASS'
    checks['netbird']=disks['netbird']
    generation=secrets.token_hex(32)
    observations={name:list(disks['observations']) for name in checks}
    observations['build']=['Source-built ISO SHA256: '+sha256(result['iso'])]
    observations['local_boot'].append('Explicit QEMU accelerator: '+qemu_accelerator)
    observations['architecture']=['Native ELF/package audit count: '+str(result['audited_packages']), 'Architecture-specific EFI PE and kernel headers verified: '+architecture]
    observations['module_signatures']=['Verified signed modules: '+str(result['signed_modules_verified']),'Matching kernel certificate SHA256: '+result['kernel_certificate_sha256']]
    observations['branding']=['Supplied SVG and os-release inspected; actual installed version output checked on both disk formats']
    observations['sanitization']=['Installed immutable root matched the input ISO; runtime overlay discarded offline; factory root sanitized and audited; journal recreated and free space zeroed/discarded']
    observations['disk_equivalence']=['Both standalone 50 GiB conversions passed qemu-img checks and source content comparison']
    observations['netbird']=['Actual native CLI commit/save, successful/failed enrollment cleanup, disable/enable, reboot identity, removal/reboot inactivity and secret scans passed for both disk-format clones'] if disks['netbird']=='PASS' else ['Required guest NetBird validation not completed']
    proof=reconstruction['proof']
    observations['corresponding_source']=[
        'Actually restored and rehashed '+str(proof['source_archives'])+' archives / '+str(proof['restored_regular_members'])+' regular source/license/recipe members; every inventory reference matched',
        'Every component binds the exact project-source.tar used by this source build: '+proof['actual_project_build_input_sha256'],
        'Source-set reconstruction; bit-identical binary reproduction is not claimed']
    evidence={'source_commit':commit,'version':version,'architecture':architecture,'generation':generation,
              'qemu_accelerator':qemu_accelerator,
              'compiler_scratch_retirement':retired,
              'factory_content_audit':source['factory_content_audit'],
              'source_set_reconstruction':reconstruction,
              'checks':{name:{'status':status,'observations':observations[name] if status=='PASS' else ['Required validation not completed']} for name,status in checks.items()}}
    evidence_path=output/f'shirube-os-{version}-{architecture}-evidence.json';evidence_path.write_text(json.dumps(evidence,indent=2)+'\n')
    files=[(result['iso'],'iso'),(disks['images']['qcow2'],'qcow2'),(disks['images']['vmdk'],'vmdk'),
           (source['inventory'],'licenses'),(evidence_path,'evidence')]+[(path,'source') for path in source['archives']]
    lock=json.loads(Path('/src/source-lock.json').read_text())
    manifest={'schema_version':1,'manifest_kind':'release','project':'Shirube OS','provider':'michibiki-io',
              'version':version,'source_commit':commit,'source_clean':True,
              'upstream':{'version':lock['stream']['version'],'sha256':lock['stream']['sha256'],'signature':'PASS',
                          'vpp_commit':lock['external_git']['vpp']['commit'],'vpp_patches_commit':lock['external_git']['vpp-patches']['commit'],
                          'substitution':'; '.join(lock['substitutions'])},
              'architectures':{architecture:{'checks':checks,'oci':'NOT_RUN','virtual_size':50*1024**3,
                  'qemu_accelerator':qemu_accelerator,
                  'standalone':True,'vmdk_subformat':'streamOptimized','signing_certificate_sha256':result['kernel_certificate_sha256'],
                  'repository_fingerprint':result['repository_fingerprint'],'generation':generation,'firmware':'UEFI',
                  'console':{'device':'ttyAMA0' if architecture=='arm64' else 'ttyS0','baud':115200},
                  'provisioning':{'method':'console-enrollment-v1','default_credentials':False}}},
              'assets':[{'name':path.name,'role':role,'architecture':architecture,'generation':generation,
                         'size':path.stat().st_size,'sha256':sha256(path)} for path,role in files]}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    names=sorted([item['name'] for item in manifest['assets']]+['manifest.json'])
    (output/'SHA256SUMS').write_text(''.join(f'{sha256(output/name)}  {name}\n' for name in names))
    # NOT_RUN/FAIL entries are intentional blockers, never automatic waivers.
    validate(output,commit,version,architectures={architecture})
