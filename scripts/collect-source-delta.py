#!/usr/bin/env python3
"""Collect source additions without compiling or certifying an existing ISO.

Run in the pinned producer container. This emits a partial source set, which
must be combined with the verified original inventory and modification sources
before source reconstruction or release validation can pass.
"""
import argparse
import json
from pathlib import Path
import re
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from producer.bundles import bundle
from producer.collect import approval_for_notice, files
from producer.debian_sources import acquire, descriptor, legal_files, unpack
from producer.debian_source_export import prepare as prepare_export
from producer.policy import require_stream_version
from producer.sources import download, extract, sha256


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delta',type=Path,required=True)
    parser.add_argument('--notices',type=Path,required=True)
    parser.add_argument('--project-source',type=Path,required=True)
    parser.add_argument('--commit',required=True)
    parser.add_argument('--architecture',choices=['amd64','arm64'],required=True)
    parser.add_argument('--version',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--work',type=Path,required=True)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1]
    lock=json.loads((repo/'source-lock.json').read_text())
    require_stream_version(args.version,lock['stream']['version'])
    if not re.fullmatch('[0-9a-f]{40}',args.commit):raise ValueError('Exact project commit required')
    with tarfile.open(args.project_source) as archive:
        if archive.pax_headers.get('comment')!=args.commit:raise ValueError('Project source commit differs')
    rows=json.loads(args.delta.read_text())['added']
    if not isinstance(rows,dict) or not rows:raise ValueError('Nonempty package additions required')
    for name,row in rows.items():
        if (not re.fullmatch('[a-z0-9][a-z0-9+.-]*',name) or not isinstance(row,list)
                or len(row)!=4 or not all(isinstance(v,str) and v for v in row)
                or row[1] not in {args.architecture,'all'}
                or not re.fullmatch('[0-9][A-Za-z0-9.+:~_-]*',row[0])
                or not re.fullmatch('[a-z0-9][a-z0-9+.-]*',row[2])
                or not re.fullmatch('[0-9][A-Za-z0-9.+:~_-]*',row[3])):
            raise ValueError('Invalid exact package addition')
        if name=='cloud-init' and (lock['cloud_init']['version']!='22.1'
                or row!=['22.1+shirube1','all','cloud-init','22.1+shirube1']):
            raise ValueError('Native cloud-init binary/source identity differs')
        notice=args.notices/name/'copyright'
        if notice.is_symlink() or not notice.is_file():raise ValueError('Regular installed notice required')
    if not args.output.is_dir() or any(args.output.iterdir()):raise ValueError('Empty owned output directory required')
    args.work.mkdir(mode=0o700)
    approvals=json.loads((repo/'license-audit.json').read_text())['components']
    exports=json.loads((repo/'containers/native/debian-source-exports.json').read_text())
    import subprocess
    subprocess.run(['apt-get','-o','APT::Update::Error-Mode=any','update'],check=True)
    requirements={(row[2],row[3]) for name,row in rows.items() if name!='cloud-init'}
    debian=acquire(requirements,args.work/'debian-inputs')
    prefix=f'shirube-os-{args.version}-{args.architecture}'
    shared_path=args.output/(prefix+'-init-build-inputs.tar.gz')
    shared=bundle([(args.project_source,'project-source.tar','build_inputs'),
                   (Path(__file__).resolve(),'collect-source-delta.py','sources'),
                   (repo/'LICENSE','LICENSE','license_files')],shared_path,1773859634)
    assets=[shared_path];components=[]
    for index,(name,row) in enumerate(sorted(rows.items())):
        version,arch,source_name,source_version=row
        notice=args.notices/name/'copyright';inputs=[];provenance={};origin_hint=None
        if name=='cloud-init':
            spec=lock['cloud_init'];original=args.work/'cloud-init-upstream.tar.gz'
            download(spec['url'],original)
            if sha256(original)!=spec['sha256']:raise ValueError('Pinned cloud-init source differs')
            extract(original,args.work/'cloud-init-original')
            source=args.work/'cloud-init-original'/('vyos-cloud-init-'+spec['commit'])
            origin=spec['sha256'];provenance={'cloud_init':spec,'packaging_recipe_sha256':sha256(repo/'producer/cloud_init.py')}
            inputs.append((original,'upstream.tar.gz','sources'))
            for member in ['LICENSE','LICENSE-GPLv3','LICENSE-Apache2.0','packages/debian/copyright',
                           'cloudinit/config/cc_vyos.py','cloudinit/config/cc_vyos_userdata.py']:
                inputs.append((source/member,'original-notices/'+member,'license_files'))
        else:
            originals=debian[(source_name,source_version)]
            dsc=descriptor(originals,source_name,source_version)
            preferred=args.work/('preferred-'+str(index))
            unpack(originals,source_name,source_version,preferred)
            exported=prepare_export(source_name,source_version,originals,preferred,exports)
            source=preferred if exported else originals
            origin=exported['sha256'] if exported else sha256(dsc)
            if exported:
                provenance['debian_preferred_source_export']=exported
                origin_hint='debian:'+exported['original_descriptor_sha256']
            for path,member in files(source):inputs.append((path,'source/'+member,'sources'))
            for path,member in legal_files(preferred):inputs.append((path,'upstream-notices/'+member,'license_files'))
        reviewed=approval_for_notice(approvals,source_name,source_version,origin,notice,repo)
        cleared=(reviewed.get('source_identity')==origin and reviewed.get('copyright_sha256')==sha256(notice)
                 and reviewed.get('redistributable') is True and bool(reviewed.get('license')))
        inputs.append((notice,'copyright','license_files'))
        recipe=args.work/('recipe-'+str(index)+'.json')
        recipe.write_text(json.dumps({'kind':'source-addition-reconstruction','project_commit':args.commit,
            'source_package':source_name,'source_version':source_version,
            'instructions':'Verify all archive members and use the included project source for the pinned packaging recipe. Debian source sets unpack with dpkg-source; cloud-init uses producer/cloud_init.py. No binary compilation was performed by this collector.'},indent=2)+'\n')
        inputs.append((recipe,'build-reconstruction.json','build_inputs'))
        archive=args.output/(prefix+f'-init-source-{index:04d}.tar.gz')
        records=bundle(inputs,archive,1773859634,source_origin=origin_hint)
        records['build_inputs'].extend(shared['build_inputs']);assets.append(archive)
        components.append({'name':name,'version':version,'architecture':arch,
            'source_package':source_name,'source_version':source_version,'source_identity':origin,
            'source_origin':provenance,'license':reviewed.get('license','UNREVIEWED'),
            'redistributable':cleared,'bundled_components':[],**records})
        print('SOURCE_ADDITION: '+name+' '+('CLEARED' if cleared else 'UNRESOLVED'),flush=True)
    result={'schema_version':1,'kind':'partial-source-additions','source_commit':args.commit,
            'release_clearance':'NOT_CLEARED','scope':'Added packages only; original component sources and initialization modification overlays remain mandatory',
            'components':components,'assets':[{'name':p.name,'role':'source','size':p.stat().st_size,'sha256':sha256(p)} for p in assets]}
    (args.output/'source-delta.json').write_text(json.dumps(result,indent=2)+'\n')
    if not all(c['redistributable'] for c in components):raise ValueError('Unresolved source addition permission')


if __name__=='__main__':
    main()
