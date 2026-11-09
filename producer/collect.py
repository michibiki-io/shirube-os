"""Collect exact shipped-package sources, licenses and reconstruction recipes."""
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
from .bundles import bundle
from .sources import sha256
from .sanitize import path_in_root

GENERATED={'.git','__pycache__','.pytest_cache','.debhelper'}
SUFFIXES={'.o','.ko','.a','.so','.cmi','.cmo','.cmx','.pyc','.deb'}


def http_dependencies(rootfs):
    from email.parser import BytesParser
    normalize=lambda value:re.sub('[-_.]+','-',value).lower()
    doc=path_in_root(rootfs,'usr/share/doc/vyos-http-api-tools')
    bom=doc/'components.json'
    data=json.loads(bom.read_text());declarations=data['components']
    lock=json.loads(Path('/src/native/http-api.lock.json').read_text())
    fixed={normalize(item['name']):item for item in lock['packages']}
    if data.get('architecture') not in {'amd64','arm64'}:
        raise ValueError('Missing HTTP dependency architecture')
    if {(normalize(r['name']),r['version']) for r in declarations}!={(name,r['version']) for name,r in fixed.items()}:
        raise ValueError('API dependency versions differ from the fixed source baseline')
    for record in declarations:
        expected=fixed[normalize(record['name'])]
        if (record['source']!=expected['source'] or
                record['wheel']!=expected['wheels'][data['architecture']]):
            raise ValueError('API original source or wheel differs from the fixed input')
    actual=set()
    site=path_in_root(rootfs,'usr/share/vyos-http-api-tools/lib/python3.11/site-packages')
    for metadata in site.glob('*.dist-info/METADATA'):
        parsed=BytesParser().parsebytes(metadata.read_bytes())
        actual.add((normalize(parsed['Name']),parsed['Version']))
    expected={(normalize(record['name']),record['version']) for record in declarations}
    if not expected or actual!=expected or len(expected)!=len(declarations):
        raise ValueError('API dependency inventory does not match installed distributions')
    result=[]
    for record in declarations:
        name=normalize(record['name'])
        if not re.fullmatch('[a-z0-9][a-z0-9-]*',name):raise ValueError('Invalid API dependency name')
        expected_path='third-party/'+record['name']+'/copyright'
        if record['license_notice']['path']!=expected_path:raise ValueError('Unexpected dependency notice path')
        notice=path_in_root(rootfs,'usr/share/doc/vyos-http-api-tools/'+expected_path)
        if not notice.is_file() or sha256(notice)!=record['license_notice']['sha256']:
            raise ValueError('Missing or differing dependency legal notice')
        result.append((record,notice,'third-party-notices/'+name+'/copyright'))
    return bom,result


def http_cargo_dependencies(rootfs, source, record):
    from .cargo_sources import inventory
    # Compare against the tracked parent declaration as well as the installed
    # BOM: a missing/transitively shortened BOM must not hide static Rust code.
    lock=json.loads(Path('/src/native/http-api.lock.json').read_text())
    fixed=next(item for item in lock['packages'] if item['name']==record['name'])
    if 'cargo_lock' not in fixed:
        if 'cargo' in record:raise ValueError('Undeclared API Cargo inventory')
        return []
    declaration={**fixed['cargo_lock'],'source_sha256':fixed['source']['sha256']}
    cargo=record.get('cargo',{})
    if cargo.get('parent')!=declaration:raise ValueError('API Cargo parent inventory differs')
    expected=inventory(source/'inputs'/fixed['source']['filename'],declaration)
    actual=cargo.get('components',[])
    if len(actual)!=len(expected):raise ValueError('API Cargo dependency inventory is incomplete')
    result=[]
    for crate,original in zip(actual,expected):
        if any(crate.get(key)!=value for key,value in original.items()):
            raise ValueError('API Cargo dependency differs from the original lock')
        name=crate['name'][6:]+'-'+crate['version']
        member=('git-archives/'+crate['sha256']+'.tar.gz' if 'git_source' in original
                else 'restored/archives/'+name+'.crate')
        notice_member='notices/'+name+'.txt'
        installed='third-party/'+record['name']+'/cargo/'+name+'/copyright'
        if (crate['source_member']!=member or crate['notice_member']!=notice_member or
                crate['license_notice']['path']!=installed):
            raise ValueError('Unexpected API Cargo source or notice path')
        prefix='cargo/'+record['name']+'/'
        archive=source/prefix/member
        notice=path_in_root(rootfs,'usr/share/doc/vyos-http-api-tools/'+installed)
        retained=source/prefix/notice_member
        if (archive.is_symlink() or sha256(archive)!=original['sha256'] or
                sha256(notice)!=crate['notice_sha256'] or sha256(retained)!=sha256(notice) or
                crate['license_notice']['sha256']!=sha256(notice)):
            raise ValueError('API Cargo original source or legal notice differs')
        result.append({'name':crate['name'],'version':crate['version'],
            'source_identity':original['sha256'],'source_sha256':original['sha256'],
            'source_member':'source/'+prefix+member,'notice':notice,
            'notice_member':'third-party-notices/'+record['name']+'/cargo/'+name+'/copyright'})
    return result


def http_rust_standard_library(rootfs,source,work):
    from .http_rust_sources import extensions,write_notices
    bom=json.loads(path_in_root(rootfs,'usr/share/doc/vyos-http-api-tools/components.json').read_text())
    record=bom.get('rust_stdlib',{})
    lock=json.loads(Path('/src/native/http-rust.lock.json').read_text())
    if (record.get('name')!='rust-stdlib' or record.get('version')!=lock['version'] or
            record.get('source')!=lock['source']):
        raise ValueError('Rust standard-library corresponding source inventory differs')
    original=source/'inputs'/lock['source']['filename']
    if original.is_symlink() or sha256(original)!=lock['source']['sha256']:
        raise ValueError('Rust standard-library original source missing or differs')
    compiler='rustc version '+lock['rustc_version_output'].removeprefix('rustc ')
    site=path_in_root(rootfs,'usr/share/vyos-http-api-tools/lib/python3.11/site-packages')
    actual=extensions(site,source,bom['components'],compiler)
    if record.get('extensions')!=actual or record.get('compiler_identity')!=compiler:
        raise ValueError('Rust standard-library source does not correspond to the shipped extensions')
    work.mkdir(parents=True,exist_ok=False)
    recreated=work/'copyright'
    members=write_notices(original,lock['version'],recreated)
    installed='third-party/rust-stdlib/copyright'
    notice=path_in_root(rootfs,'usr/share/doc/vyos-http-api-tools/'+installed)
    if (record.get('source_legal_members')!=members or record.get('license_notice')!={'path':installed,'sha256':sha256(recreated)}
            or record.get('notice_sha256')!=sha256(recreated) or sha256(notice)!=sha256(recreated)
            or sha256(source/'rust-notices/copyright')!=sha256(recreated)):
        raise ValueError('Rust standard-library original legal notices are incomplete or differ')
    return {'name':'rust-stdlib','version':lock['version'],'source_identity':lock['source']['sha256'],
        'source_sha256':lock['source']['sha256'],'source_member':'source/inputs/'+original.name,
        'notice':notice,'notice_member':'third-party-notices/rust-stdlib/copyright'}


def netbird_dependencies(rootfs,correspondence,arch):
    prefix='usr/share/doc/shirube-integration/'
    bom=path_in_root(rootfs,prefix+'netbird-components.json')
    if not bom.is_file() or sha256(bom)!=sha256(correspondence/'components.json'):
        raise ValueError('Installed NetBird dependency inventory differs from build inputs')
    data=json.loads(bom.read_text())
    if (data['architecture']!=arch or data['restored_offline_component_rebuild']!='PASS'
            or data['reference_binary_sha256']!=sha256(path_in_root(rootfs,'usr/libexec/shirube/netbird'))):
        raise ValueError('NetBird source proof does not match the shipped binary')
    mapping=correspondence/'internal-packages.json'
    if not mapping.is_file() or sha256(mapping)!=data.get('internal_package_map_sha256'):
        raise ValueError('Actual NetBird client source/license package map is missing or differs')
    selected=json.loads(mapping.read_text())
    if (not selected['packages'] or len(selected['packages'])!=data.get('internal_package_count')
            or selected['agpl_directory_packages'] or data.get('agpl_directory_packages')!=[]):
        raise ValueError('NetBird directory-license coverage requires additional audit')
    children=[];seen=set()
    for record in data['linked_modules']:
        identity=(record['module'],record['version'])
        if identity in seen:raise ValueError('Duplicate linked NetBird module')
        seen.add(identity)
        member=record['source']['path'];notice_member=record['license_notice']['path']
        if not re.fullmatch(r'inputs/[0-9a-f]{64}\.zip',member) or not re.fullmatch(r'notices/[0-9a-f]{64}\.txt',notice_member):
            raise ValueError('Invalid linked module input path')
        notice=path_in_root(rootfs,prefix+'netbird-licenses/'+notice_member)
        if (not notice.is_file() or sha256(notice)!=record['license_notice']['sha256']
                or sha256(correspondence/'linked'/member)!=record['source']['sha256']):
            raise ValueError('Missing or differing linked module source/notice')
        children.append({'name':'go:'+record['module'],'version':record['version'],
            'source_identity':record['source_identity'],'source_member':'netbird/linked/'+member,
            'source_sha256':record['source']['sha256'],'notice':notice,
            'notice_member':'netbird/linked/'+notice_member})
    if not children:raise ValueError('Empty linked NetBird module inventory')
    notice=path_in_root(rootfs,prefix+'netbird-licenses/go-copyright')
    if not notice.is_file() or sha256(notice)!=data['go_notice_sha256']:
        raise ValueError('Missing or differing Go source legal notices')
    children.append({'name':'go-stdlib','version':data['compiler'],'source_identity':data['go_source_sha256'],
        'source_member':'netbird/inputs/go-source.tar.gz','source_sha256':data['go_source_sha256'],
        'notice':notice,'notice_member':'netbird/go-copyright'})
    return children


def approval_for(approvals,name,version,identity,notice_root=Path('/src')):
    key=name+'@'+version
    approval=approvals.get(key+'#'+identity,approvals.get(key,{}))
    for notice in approval.get('required_notice_files',[]):
        relative=Path(notice['path'])
        if (not relative.parts or relative.is_absolute() or '..' in relative.parts or
                not (relative.parts[0]=='LICENSES' or str(relative) in {'NOTICE','LICENSE'})):
            raise ValueError('Unsafe required distribution notice path')
        path=Path(notice_root)/relative
        if (path.is_symlink() or not path.is_file() or
                not path.resolve().is_relative_to(Path(notice_root).resolve()) or
                sha256(path)!=notice['sha256']):
            return {}
    return approval


def files(root, exclude=()):
    root=Path(root).resolve()
    excluded=[Path(member) for member in exclude]
    def omitted(relative):
        return any(relative==member or member in relative.parents for member in excluded)
    for directory,children,names in os.walk(root,followlinks=False):
        base=Path(directory)
        children[:]=sorted(name for name in children if name not in GENERATED
                           and not omitted((base/name).relative_to(root)))
        for name in sorted(names):
            path=base/name;relative=path.relative_to(root)
            if name in GENERATED or omitted(relative):continue
            if name.startswith('.') and name.endswith('.cmd'):continue
            if name in {'signing_key.pem','vmlinux','System.map'} or path.suffix in SUFFIXES:continue
            if path.is_file():
                # os.walk does not enter directory symlinks. Resolve file
                # aliases only, avoiding repeated ancestor lookups per file.
                resolved=path.resolve() if path.is_symlink() else path
                if not resolved.is_relative_to(root):raise ValueError('Source symlink escapes component')
                yield resolved,str(relative)


def shared_build_inputs(project,builder,patches,destination):
    """Retain shared upstream recipes/patches once, with all component bindings.

    Project scripts alone cannot reconstruct the generated kernel configuration,
    ISO defaults or VPP patches used by the public build.
    """
    inputs=[(Path(project),'project-source.tar','build_inputs')]
    for prefix,root in [('stream-builder',Path(builder)),('vpp-patches',Path(patches))]:
        if not root.is_dir() or not (root/'LICENSE').is_file():
            raise ValueError('Required shared upstream build source/license is missing')
        for path,relative in files(root):
            legal=path.name.lower().startswith(('license','licence','copying','copyright','notice'))
            inputs.append((path,prefix+'/'+relative,'license_files' if legal else 'sources'))
    records=bundle(inputs,destination,1773859634)
    return [reference for category in records.values() for reference in category]


def tracked_source_files(root):
    """Export patched tracked source, materializing confined directory aliases."""
    root=Path(root).resolve()
    names=subprocess.check_output(['git','-C',str(root),'ls-files','-z']).decode().split('\0')
    for name in sorted(filter(None,names)):
        path=Path(name)
        if path.is_absolute() or '..' in path.parts or '.git' in path.parts:
            raise ValueError('Unsafe tracked source path')
        source=root/path
        if source.is_symlink():
            if not source.resolve().is_relative_to(root):raise ValueError('External VPP source link')
            if source.is_dir():
                # Materialize this confined directory alias from the same
                # tracked tree; archives intentionally contain regular files.
                prefix=source.resolve().relative_to(root)
                for tracked in sorted(filter(None,names)):
                    try:relative=Path(tracked).relative_to(prefix)
                    except ValueError:continue
                    target=root/tracked
                    if target.is_file():
                        if not target.resolve().is_relative_to(root):raise ValueError('External VPP source link')
                        yield target.resolve(),name+'/'+str(relative)
                continue
        if not source.is_file():raise ValueError('Missing patched tracked source')
        yield source.resolve(),name


def vpp_source_files(root):
    """Export patched tracked source and original external inputs, not build trees."""
    root=Path(root).resolve()
    yield from tracked_source_files(root)
    cache=root/'build/external/downloads'
    if not cache.is_dir():raise ValueError('Original VPP external source inputs missing')
    found=False
    for source,relative in files(cache):
        found=True;yield source,'build/external/downloads/'+relative
    if not found:raise ValueError('Empty VPP external source input set')



def static_i2util(output,arch,version,project,shared):
    """OWAMP links I2util statically even when no I2util package is installed."""
    source=Path('/extra/base/i2util')
    lock=json.loads(Path('/src/native/external-sources.lock.json').read_text())['i2util']
    actual=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    if actual!=lock['commit']:raise ValueError('Static I2util source revision differs')
    notice=source/'debian/copyright'
    if not notice.is_file():raise ValueError('Static I2util notice missing')
    inputs=[(path,'source/'+relative,'sources') for path,relative in tracked_source_files(source)]
    inputs.extend([(notice,'copyright','license_files'),(source/'LICENSE','upstream-LICENSE','license_files'),
                   (project,'project-source.tar','build_inputs')])
    archive=Path(output)/f'shirube-os-{version}-{arch}-static-i2util-source.tar.gz'
    records=bundle(inputs,archive,1773859634)
    records['build_inputs'].extend(shared)
    identity=hashlib.sha256(json.dumps(lock,sort_keys=True).encode()).hexdigest()
    return archive,{'name':'i2util','version':'4.4.6-2','source_identity':identity,
                    'notice':notice,'notice_member':'copyright',**records}


def distribution_notices(project):
    """Give every component an explicit binding to retained distributor notices."""
    project=Path(project)
    if (project/'LICENSES').is_symlink() or not (project/'LICENSES').is_dir():
        raise ValueError('Missing regular distributor license directory')
    for member in ['LICENSE','NOTICE']:
        path=project/member
        if not path.is_file() or path.is_symlink():
            raise ValueError('Missing distributor license/notice')
        yield path,member
    for path,relative in files(project/'LICENSES'):
        yield path,'LICENSES/'+relative


def verify_distribution_notices(rootfs, project):
    prefix='usr/share/doc/shirube-integration/'
    for original,member in distribution_notices(project):
        installed=('distributor-LICENSE' if member=='LICENSE' else
                   'distribution-licenses/'+member.removeprefix('LICENSES/') if member.startswith('LICENSES/') else member)
        actual=path_in_root(rootfs,prefix+installed)
        if not actual.is_file() or sha256(actual)!=sha256(original):
            raise ValueError('Required distributor notice is absent or differs in the image')


def collect(rootfs, package_output, output, arch, version, work):
    rootfs,package_output,output,work=map(Path,[rootfs,package_output,output,work]);work.mkdir()
    verify_distribution_notices(rootfs,Path('/src'))
    subprocess.run(['apt-get','-o','Acquire::Retries=3','-o','APT::Update::Error-Mode=any','update'],check=True)
    listing=subprocess.check_output(['dpkg-query','--admindir='+str(rootfs/'var/lib/dpkg'),'-W',
        '-f=${Package}\t${Version}\t${Architecture}\t${source:Package}\t${source:Version}\n'],text=True)
    custom={};native_preferred={};native_jobs={}
    catalog=json.loads(Path('/src/native/packages.json').read_text())
    for job in catalog['jobs']:
        for deb in (package_output/job['name']).glob('*.deb'):
            name=subprocess.check_output(['dpkg-deb','-f',str(deb),'Package'],text=True).strip()
            custom[name]=Path('/build-private')/job['name']/'source'
            native_preferred[name]=Path('/sources')/job['source']
            native_jobs[name]=job['name']
    extras={
      'vpp':'vpp/vpp','libvpp':'vpp/vpp','nat-rtsp':'modules/rtsp','vyos-ipt-netflow':'modules/netflow',
      'openvpn-dco':'modules/ovpn','jool':'modules/jool','accel-ppp-ng':'accel/accel','hsflowd':'accel/sflow',
      'isc-kea':'kea/isc-kea','salt':'salt/salt','podman':'podman/podman','telegraf':'go/telegraf',
      'node-exporter':'go/node-exporter','frr-exporter':'go/frr-exporter','blackbox-exporter':'go/blackbox-exporter',
      'vyos-utils':'base/utils','python3-pyhumps':'base/humps','ddclient':'base/ddclient','i2util':'base/i2util',
      'libi2util':'base/i2util','owamp':'base/owamp','openvpn-otp':'base/otp','udp-broadcast-relay':'base/relay','vyos-libpam-radius-auth':'base/pam'}
    # Bind custom source recipes to exact built binary identities. A Debian
    # package with a similar source/name must use its own exact Debian source.
    produced={}
    for deb in Path('/debs').glob('*.deb'):
        values=subprocess.check_output(['dpkg-deb','--show',
            '--showformat=${Package}\t${Version}\t${Architecture}',str(deb)],text=True).split('\t')
        if len(values)!=3:raise ValueError('Invalid produced package identity')
        produced.setdefault(values[0],set()).add((values[1],values[2]))
    audit_path=Path('/src/license-audit.json')
    approvals=json.loads(audit_path.read_text())['components'] if audit_path.is_file() else {}
    shipped=[];components=[];assets=[];cached={};static={}
    project=Path('/project-source.tar')
    if not project.is_file():raise ValueError('Exact project reconstruction source archive missing')
    shared_archive=output/f'shirube-os-{version}-{arch}-shared-build-inputs.tar.gz'
    shared=shared_build_inputs(project,Path('/sources/vyos-build/vyos-build'),
        Path('/extra/vpp/patches'),shared_archive)
    assets.append(shared_archive)
    from .ocaml_sources import retain as retain_ocaml
    ocaml_archive=output/f'shirube-os-{version}-{arch}-ocaml-source.tar.gz'
    ocaml_records,ocaml_dependencies=retain_ocaml(Path('/opt/opam'),ocaml_archive,work/'ocaml',files)
    assets.append(ocaml_archive)
    source_identity=json.loads(Path('/src/source-lock.json').read_text())['stream']['sha256']
    for line in listing.splitlines():
        name,binary_version,architecture,source_name,source_version=line.split('\t')
        print('SOURCE: '+name+' '+binary_version+' '+architecture,flush=True)
        package={'name':name,'version':binary_version,'architecture':architecture};shipped.append(package)
        source_name=source_name or name;source_version=source_version or binary_version
        copyright_file=path_in_root(rootfs,'usr/share/doc/'+name+'/copyright')
        if not copyright_file.is_file() or not copyright_file.read_bytes().strip():raise ValueError('Exact shipped copyright/license notice missing or empty')
        key=(source_name,source_version,sha256(copyright_file))
        if key not in cached:
            component=work/re.sub('[^A-Za-z0-9_.-]','_',source_name+'-'+source_version+'-'+str(len(cached)));component.mkdir()
            matching=(binary_version,architecture) in produced.get(name,set())
            if name in produced and not matching:raise ValueError('Installed custom package differs from produced binary version')
            source=custom.get(name) if matching else None
            if source is None and matching:
                match=next((path for prefix,path in extras.items()
                    if name.startswith(prefix) or source_name.startswith(prefix)),None)
                if match:source=Path('/extra')/match
            inputs=[];nested=[]
            if source_name=='vyos-1x':nested.extend(ocaml_dependencies)
            if source_name=='owamp':
                if 'i2util' not in static:
                    archive,dependency=static_i2util(output,arch,version,project,shared)
                    assets.append(archive);static['i2util']=dependency
                nested.append(static['i2util'])
            if name=='shirube-integration':
                source=Path('/src/guest');origin=source_identity
                lock=json.loads(Path('/src/source-lock.json').read_text())['netbird']
                correspondence=Path('/guest-package/netbird-correspondence')
                nested=netbird_dependencies(rootfs,correspondence,arch)
                for path,relative in files(correspondence):
                    category=('license_files' if relative.startswith('linked/notices/') or relative=='go-copyright'
                              else 'sources' if relative.startswith(('inputs/','linked/inputs/')) or relative=='vendor-source.tar.gz'
                              else 'build_inputs')
                    inputs.append((path,'netbird/'+relative,category))
            elif source is not None:
                origin=source_identity
            else:
                subprocess.run(['apt-get','source','--download-only','--only-source',source_name+'='+source_version],cwd=component,check=True)
                from .debian_sources import unpack,legal_files
                preferred=work/('debian-source-tree-'+str(len(cached)))
                dsc=unpack(component,source_name,source_version,preferred)
                origin=sha256(dsc);source=component
                for legal_file,member in legal_files(preferred):
                    inputs.append((legal_file,'upstream-notices/'+member,'license_files'))
            if not source.is_dir():raise ValueError('Corresponding custom source directory missing')
            kind='vpp' if str(source)=='/extra/vpp/vpp' else 'vyos-1x' if source_name=='vyos-1x' else 'salt' if source_name=='salt' else None
            # Compilation/test trees contain generated keys, binaries and
            # toolchain links. Native packages retain the authenticated original
            # preferred source plus the exact shared/local patch recipes. API
            # dependency archives are acquired by its declared source recipe.
            preferred_root=native_preferred.get(name) if matching and name!='vyos-http-api-tools' else None
            if preferred_root is not None:
                if not preferred_root.is_dir():raise ValueError('Missing authenticated native preferred source')
                kernel_outputs=('debian','include/generated','arch/arm64/include/generated',
                                'arch/x86/include/generated','certs/signing_key.x509',
                                'arch/arm64/boot/Image','arch/arm64/boot/Image.gz',
                                'arch/x86/boot/bzImage','vmlinux.btf','Module.symvers',
                                'modules.order','modules.builtin','modules.builtin.modinfo')
                source_files=files(preferred_root,exclude=kernel_outputs if native_jobs[name]=='kernel' else ())
                for member in ['kernel.config','recipe-checksums.txt','kernel-tool-patches.sha256','manifest.json']:
                    build_input=package_output/native_jobs[name]/member
                    if build_input.is_file():inputs.append((build_input,'native-build/'+member,'build_inputs'))
            else:
                source_files=vpp_source_files(source) if kind=='vpp' else tracked_source_files(source) if kind in {'vyos-1x','salt'} else files(source)
            if kind=='salt':
                # Build virtualenvs and generated SDK archives are not the
                # preferred Salt source. Keep the tracked input and the exact
                # separately pinned packaging-tool source instead.
                for path,relative in tracked_source_files(source/'python-tools-scripts'):
                    inputs.append((path,'build-tools/python-tools-scripts/'+relative,'build_inputs'))
                if 'salt-sdk' not in static:
                    from .salt_sources import retain as retain_salt
                    sdk_archive=output/f'shirube-os-{version}-{arch}-salt-sdk-source.tar.gz'
                    static['salt-sdk']=retain_salt(rootfs,sdk_archive,work/'salt-sdk',
                                                   Path('/src/native/salt-sources.lock.json'))
                    assets.append(sdk_archive)
                nested.extend(static['salt-sdk'][1])
            binary_map=source/'shirube-corresponding-inputs/binary-info.json'
            generated={Path(item['path']).name:item['sha256'] for item in json.loads(binary_map.read_text())} if binary_map.is_file() else {}
            for path,relative in source_files:
                if (source/'shirube-corresponding-inputs/inventory.json').is_file():
                    if relative.startswith('package/'):continue
                    if relative in generated and sha256(path)==generated[relative]:continue
                category='license_files' if relative.startswith('shirube-corresponding-inputs/notices/') or relative=='shirube-corresponding-inputs/go-copyright' else 'sources'
                inputs.append((path,'source/'+relative,category))
            if (source/'shirube-corresponding-inputs/inventory.json').is_file():
                from .runtime_sources import dependencies as runtime_dependencies
                nested.extend(runtime_dependencies(rootfs,source,name))
            if name=='vyos-http-api-tools':
                bom,dependencies=http_dependencies(rootfs)
                inputs.append((bom,'bundled-components.json','build_inputs'))
                for dependency,notice,member in dependencies:
                    inputs.append((notice,member,'license_files'))
                    nested.append({'name':'pypi:'+re.sub('[-_.]+','-',dependency['name']).lower(),
                        'version':dependency['version'],'source_identity':dependency['source']['sha256'],
                        'source_member':'source/inputs/'+dependency['source']['filename'],
                        'source_sha256':dependency['source']['sha256'],'notice':notice,'notice_member':member,
                        'binary_input':dependency['wheel']})
                    for crate in http_cargo_dependencies(rootfs,source,dependency):
                        inputs.append((crate['notice'],crate['notice_member'],'license_files'))
                        nested.append(crate)
                standard=http_rust_standard_library(rootfs,source,work/'http-rust-standard-library')
                inputs.append((standard['notice'],standard['notice_member'],'license_files'))
                nested.append(standard)
            cached_recipe=source.parent/'cache-build-inputs'
            if cached_recipe.is_dir():
                for path,relative in files(cached_recipe):inputs.append((path,'library-cache/'+relative,'build_inputs'))
            inputs.extend([(copyright_file,'copyright','license_files'),(project,'project-source.tar','build_inputs')])
            for notice,member in distribution_notices(Path('/src')):
                inputs.append((notice,'distribution-notices/'+member,'license_files'))
            archive=output/f'shirube-os-{version}-{arch}-source-{len(cached):04d}.tar.gz'
            common=rootfs/'usr/share/common-licenses'
            if common.is_dir():
                for legal_file,relative in files(common):
                    inputs.append((legal_file,'common-licenses/'+relative,'license_files'))
            fixture_origin=('vpp:'+json.loads(Path('/src/source-lock.json').read_text())['external_git']['vpp']['commit']
                if kind=='vpp' else 'stream:'+source_identity if preferred_root is not None or kind in {'vyos-1x','salt'} or source_name in {'telegraf','ddclient'} else None)
            records=bundle(inputs,archive,1773859634,source_origin=fixture_origin)
            records['build_inputs'].extend(shared)
            if kind=='salt':records['build_inputs'].extend(static['salt-sdk'][0]['build_inputs'])
            if source_name=='vyos-1x':records['build_inputs'].extend(ocaml_records['build_inputs'])
            external=json.loads(Path('/src/native/external-sources.lock.json').read_text())
            keys=[]
            location=str(source)
            for prefix, names in [('/extra/vpp/',['vpp','vpp-patches']),('/extra/modules/rtsp',['nat-rtsp']),('/extra/modules/netflow',['ipt-netflow']),('/extra/base/utils',['vyos-utils']),('/extra/base/i2util',['i2util']),('/extra/kea/',['kea-packaging']),('/extra/salt/',['salt-tools'])]:
                if location.startswith(prefix):keys.extend(names)
            provenance={'stream_archive_sha256':source_identity,'external_git':{key:external[key] for key in keys}}
            if preferred_root is not None:
                provenance.update(preferred_stream_member=str(preferred_root.relative_to('/sources')),
                                  native_job=native_jobs[name])
            if name=='shirube-integration':
                provenance={'netbird_source_commit':lock['source_commit'],'netbird_source_sha256':lock['source_sha256'],'headless_asset_sha256':lock['assets'][arch]['sha256'],'integration_license_sha256':sha256(Path('/src/LICENSE'))}
                origin=hashlib.sha256(json.dumps(provenance,sort_keys=True).encode()).hexdigest()
            if keys:origin=hashlib.sha256(json.dumps(provenance,sort_keys=True).encode()).hexdigest()
            if name=='vyos-http-api-tools':
                provenance={'packaging_recipe_sha256':sha256(Path('/src/native/http_api.py')),
                    'dependency_lock_sha256':sha256(Path('/src/native/http-api.lock.json')),
                    'packaging_license_sha256':sha256(Path('/src/LICENSE')),
                    'dependency_baseline':json.loads(Path('/src/native/http-api.lock.json').read_text())['dependency_baseline']}
                origin=hashlib.sha256(json.dumps(provenance,sort_keys=True).encode()).hexdigest()
            children=[]
            for dependency in nested:
                reviewed=approval_for(approvals,dependency['name'],dependency['version'],dependency['source_identity'])
                child_clear=(reviewed.get('source_identity')==dependency['source_identity']
                    and reviewed.get('copyright_sha256')==sha256(dependency['notice'])
                    and reviewed.get('redistributable') is True
                    and isinstance(reviewed.get('license'),str) and bool(reviewed.get('license')))
                if 'sources' in dependency:
                    source_refs=dependency['sources'];legal_records=dependency['license_files']
                    if not source_refs:raise ValueError('Exact bundled dependency sources are missing')
                elif 'source_members' in dependency:
                    expected={(item['member'],item['sha256']) for item in dependency['source_members']}
                    source_refs=[record for record in records['sources'] if (record['member'],record['sha256']) in expected]
                    if len(source_refs)!=len(expected):raise ValueError('Vendored runtime dependency source is incomplete')
                    legal_records=records['license_files']
                else:
                    source_refs=[record for record in records['sources'] if record['member']==dependency['source_member'] and record['sha256']==dependency['source_sha256']]
                    if len(source_refs)!=1:raise ValueError('Exact bundled dependency source is missing')
                    legal_records=records['license_files']
                notice_refs=[record for record in legal_records if record['member']==dependency['notice_member'] and record['sha256']==sha256(dependency['notice'])]
                if len(notice_refs)!=1:raise ValueError('Exact bundled dependency legal notice is missing')
                children.append({'name':dependency['name'],'version':dependency['version'],
                    'source_identity':dependency['source_identity'],'license':reviewed.get('license','UNREVIEWED'),
                    'redistributable':child_clear,'sources':source_refs,
                    'license_files':legal_records if 'sources' in dependency else notice_refs,
                    'build_inputs':list({(item['asset'],item['member'],item['sha256']):item
                        for item in dependency.get('build_inputs',[])+records['build_inputs']}.values()),
                    **({'binary_input':dependency['binary_input']} if 'binary_input' in dependency else {})})
            approval=approval_for(approvals,source_name,source_version,origin)
            cleared=(approval.get('source_identity')==origin and approval.get('redistributable') is True
                     and approval.get('copyright_sha256')==sha256(copyright_file)
                     and isinstance(approval.get('license'),str) and bool(approval.get('license')))
            cleared=cleared and all(child['redistributable'] for child in children)
            cached[key]=(records,approval.get('license','UNREVIEWED'),cleared,origin,provenance,children)
            assets.append(archive)
        records,license_name,cleared,origin,provenance,children=cached[key]
        components.append({**package,'source_package':source_name,'source_version':source_version,
                           'source_identity':origin,'source_origin':provenance,'license':license_name,'redistributable':cleared,
                           'bundled_components':children,**records})
    inventory={'schema_version':1,'shipped_packages':shipped,'components':components}
    path=output/f'shirube-os-{version}-{arch}-licenses.json';path.write_text(json.dumps(inventory,indent=2)+'\n')
    return {'inventory':path,'archives':assets,'cleared':all(x['redistributable'] for x in components)}
