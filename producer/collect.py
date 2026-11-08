"""Collect exact shipped-package sources, licenses and reconstruction recipes."""
import json
import hashlib
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
    declarations=json.loads(bom.read_text())['components']
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


def approval_for(approvals,name,version,identity):
    key=name+'@'+version
    return approvals.get(key+'#'+identity,approvals.get(key,{}))


def files(root):
    root=Path(root).resolve()
    for path in sorted(root.rglob('*')):
        relative=path.relative_to(root)
        if any(part in GENERATED for part in relative.parts):continue
        if path.name in {'signing_key.pem','vmlinux','System.map'} or path.suffix in SUFFIXES:continue
        if path.is_file():
            resolved=path.resolve()
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


def collect(rootfs, package_output, output, arch, version, work):
    rootfs,package_output,output,work=map(Path,[rootfs,package_output,output,work]);work.mkdir()
    listing=subprocess.check_output(['dpkg-query','--admindir='+str(rootfs/'var/lib/dpkg'),'-W',
        '-f=${Package}\t${Version}\t${Architecture}\t${source:Package}\t${source:Version}\n'],text=True)
    custom={}
    catalog=json.loads(Path('/src/native/packages.json').read_text())
    for job in catalog['jobs']:
        for deb in (package_output/job['name']).glob('*.deb'):
            name=subprocess.check_output(['dpkg-deb','-f',str(deb),'Package'],text=True).strip()
            custom[name]=Path('/build-private')/job['name']/'source'
    extras={
      'vpp':'vpp/vpp','libvpp':'vpp/vpp','nat-rtsp':'modules/rtsp','vyos-ipt-netflow':'modules/netflow',
      'openvpn-dco':'modules/ovpn','jool':'modules/jool','accel-ppp':'accel/accel','hsflowd':'accel/sflow',
      'isc-kea':'kea/isc-kea','salt':'salt/salt','podman':'podman/podman','telegraf':'go/telegraf',
      'node-exporter':'go/node-exporter','frr-exporter':'go/frr-exporter','blackbox-exporter':'go/blackbox-exporter',
      'vyos-utils':'base/utils','python3-humps':'base/humps','ddclient':'base/ddclient','i2util':'base/i2util',
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
                dsc=list(component.glob('*.dsc'))
                if len(dsc)!=1:raise ValueError('Exact Debian source descriptor missing')
                origin=sha256(dsc[0]);source=component
            if not source.is_dir():raise ValueError('Corresponding custom source directory missing')
            kind='vpp' if str(source)=='/extra/vpp/vpp' else 'vyos-1x' if source_name=='vyos-1x' else None
            source_files=vpp_source_files(source) if kind=='vpp' else tracked_source_files(source) if kind=='vyos-1x' else files(source)
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
            cached_recipe=source.parent/'cache-build-inputs'
            if cached_recipe.is_dir():
                for path,relative in files(cached_recipe):inputs.append((path,'library-cache/'+relative,'build_inputs'))
            inputs.extend([(copyright_file,'copyright','license_files'),(project,'project-source.tar','build_inputs')])
            archive=output/f'shirube-os-{version}-{arch}-source-{len(cached):04d}.tar.gz'
            common=rootfs/'usr/share/common-licenses'
            if common.is_dir():
                for legal_file,relative in files(common):
                    inputs.append((legal_file,'common-licenses/'+relative,'license_files'))
            fixture_origin=('vpp:'+json.loads(Path('/src/source-lock.json').read_text())['external_git']['vpp']['commit']
                if kind=='vpp' else 'stream:'+source_identity if kind=='vyos-1x' or source_name=='telegraf' else None)
            records=bundle(inputs,archive,1773859634,source_origin=fixture_origin)
            records['build_inputs'].extend(shared)
            if source_name=='vyos-1x':records['build_inputs'].extend(ocaml_records['build_inputs'])
            external=json.loads(Path('/src/native/external-sources.lock.json').read_text())
            keys=[]
            location=str(source)
            for prefix, names in [('/extra/vpp/',['vpp','vpp-patches']),('/extra/modules/rtsp',['nat-rtsp']),('/extra/modules/netflow',['ipt-netflow']),('/extra/base/utils',['vyos-utils']),('/extra/base/i2util',['i2util']),('/extra/kea/',['kea-packaging']),('/extra/salt/',['salt-tools'])]:
                if location.startswith(prefix):keys.extend(names)
            provenance={'stream_archive_sha256':source_identity,'external_git':{key:external[key] for key in keys}}
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
