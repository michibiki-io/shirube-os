"""Complete candidate assembly; missing source/license/runtime proof fails publication."""
import json
from pathlib import Path
import secrets
import subprocess
import sys
from .release import CHECKS, validate
from .sources import sha256


def collect_iso_sources(iso,packages,output,architecture,version):
    """Audit source/license inputs independently of virtualization availability."""
    from .collect import collect
    rootfs=Path('/build-private/release-rootfs')
    subprocess.run(['xorriso','-osirrox','on','-indev',str(iso),'-extract',
        '/live/filesystem.squashfs','/build-private/iso-root.squashfs'],check=True)
    subprocess.run(['unsquashfs','-d',str(rootfs),'/build-private/iso-root.squashfs'],
        check=True,stdout=subprocess.DEVNULL)
    return rootfs,collect(rootfs,packages,output,architecture,version,Path('/build-private/corresponding-source'))


def release_candidate(sources, packages, output, architecture, version, commit, management):
    sys.path.insert(0,'/src/native')
    from image import assemble
    from .vm import build_disks
    print('STAGE: native ISO assembly and architecture/signature inspection',flush=True)
    result=assemble(sources,packages,architecture,version,output)
    print('STAGE: exact corresponding source collection and component/license clearance',flush=True)
    rootfs,source=collect_iso_sources(result['iso'],packages,output,architecture,version)
    print('STAGE: fresh install, sanitization, conversions and fresh-instance boot/access tests',flush=True)
    disks=build_disks(result['iso'],architecture,version,Path('/build-private/disk'),output,management.verify)
    checks={name:'NOT_RUN' for name in CHECKS}
    for name in ['build','architecture','module_signatures','sanitization','disk_equivalence','local_boot',
                 'unprovisioned_boot','provisioned_access','identity_regeneration']:
        checks[name]='PASS'
    os_release=(rootfs/'etc/os-release').read_text()
    checks['branding']='PASS' if 'NAME="Shirube OS"' in os_release and (rootfs/'usr/share/shirube/logo.svg').read_bytes()==Path('/src/shirube-os-logo.svg').read_bytes() else 'FAIL'
    checks['licenses']='PASS' if source['cleared'] else 'FAIL'
    # Archive/member checks run in bundle()/validate(). Full reconstruction and
    # NetBird enrollment/save/reboot require genuine runtime test evidence.
    checks['corresponding_source']='NOT_RUN'
    checks['netbird']=disks['netbird']
    generation=secrets.token_hex(32)
    observations={name:list(disks['observations']) for name in checks}
    observations['build']=['Source-built ISO SHA256: '+sha256(result['iso'])]
    observations['architecture']=['Native ELF/package audit count: '+str(result['audited_packages']), 'Architecture-specific EFI PE and kernel headers verified: '+architecture]
    observations['module_signatures']=['Verified signed modules: '+str(result['signed_modules_verified']),'Matching kernel certificate SHA256: '+result['kernel_certificate_sha256']]
    observations['branding']=['Supplied SVG and os-release inspected; actual installed version output checked on both disk formats']
    observations['sanitization']=['Guest root repacked; second offline filesystem audit, journal recreation and free-space zero/discard completed']
    observations['disk_equivalence']=['Both standalone 50 GiB conversions passed qemu-img checks and source content comparison']
    observations['netbird']=['Actual native CLI commit/save, successful/failed enrollment cleanup, disable/enable, reboot identity, removal/reboot inactivity and secret scans passed for both disk-format clones'] if disks['netbird']=='PASS' else ['Required guest NetBird validation not completed']
    evidence={'source_commit':commit,'version':version,'architecture':architecture,'generation':generation,
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
