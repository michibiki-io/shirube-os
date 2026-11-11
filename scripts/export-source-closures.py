#!/usr/bin/env python3
"""Export exact, architecture-independent preferred-source closure additions.

This never changes binaries or certifies the target OS. A consumer must bind
the target's actual binary identities, retained build inputs and notices,
verify every imported source origin, and restore the complete source set.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import sys
import tarfile
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from producer.bundles import bundle
from producer.release import filename
from producer.sources import sha256


def select(inventory, rows, parent_names, child_gaps):
    parents={c['name']:c for c in inventory['components']}
    selected={}
    for name in parent_names:
        c=parents[name];row=rows[name]
        if [c['source_package'],c['source_version']]!=row[2:]:
            raise ValueError('Preferred source version differs from target')
        selected[name]=copy.deepcopy(c)
    gaps={(c['name'],c['version']) for c in child_gaps}
    children={}
    for parent in parents.values():
        for c in parent.get('bundled_components',[]):
            identity=(c['name'],c['version'])
            if identity in gaps:
                previous=children.get(identity)
                # Repeated SDK closures may bind different parent recipes;
                # preferred source bytes and legal notices must agree.
                if previous and any({r['sha256'] for r in previous[k]}!={r['sha256'] for r in c[k]}
                                    for k in ['sources','license_files']):
                    raise ValueError('Ambiguous canonical dependency closure')
                children.setdefault(identity,copy.deepcopy(c))
    if children.keys()!=gaps:raise ValueError('Canonical dependency closure missing')
    vpp=parents['vpp'];integration=parents['shirube-integration']
    geoip=[copy.deepcopy(c) for c in integration['bundled_components']
           if c['name']=='data:dbip-country-lite']
    if len(geoip)!=1:raise ValueError('One exact GeoIP source closure required')
    return {'parents':selected,'children':list(children.values()),
            'vpp':{k:copy.deepcopy(vpp[k]) for k in ['source_identity','source_origin','bundled_components']},
            'geoip':{'origin':{k:v for k,v in integration['source_origin'].items() if k.startswith('geoip_')},
                     'children':geoip}}


def reference_groups(fragment):
    groups={}
    def add(reference,category):
        asset=filename(reference['asset']);member=reference['member'];p=Path(member)
        if (p.is_absolute() or '..' in p.parts or '.git' in p.parts or str(p)!=member or '\\' in member
                or not re.fullmatch('[0-9a-f]{64}',reference['sha256'])):
            raise ValueError('Unsafe preferred-source reference')
        prior=groups.setdefault(asset,{}).get(member)
        if prior and prior['sha256']!=reference['sha256']:raise ValueError('Conflicting preferred-source reference')
        rank={'sources':0,'license_files':1,'build_inputs':2}
        if not prior or rank[category]<rank[prior['category']]:
            groups[asset][member]={'sha256':reference['sha256'],'category':category}
    def visit(component,*,vpp=False):
        if component.get('redistributable') is not True:raise ValueError('Uncleared canonical source closure')
        for category in ['sources','license_files','build_inputs']:
            for ref in component.get(category,[]):
                # VPP raw archives and ARM recipes come from the retained ARM
                # input set. Only missing preferred inputs and their notices
                # are imported from this architecture-independent supplement.
                if vpp and (ref['member'].startswith('source/build/external/downloads/') or category=='build_inputs'):
                    continue
                add(ref,category)
        for child in component.get('bundled_components',[]):visit(child,vpp=vpp)
    for c in fragment['parents'].values():visit(c)
    for c in fragment['children']+fragment['geoip']['children']:visit(c)
    for c in fragment['vpp']['bundled_components']:visit(c,vpp=True)
    return groups


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['canonical','packages','child-gaps','output']:p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--parent',action='append',required=True)
    p.add_argument('--architecture',choices=['amd64','arm64'],required=True)
    args=p.parse_args();repo=Path(__file__).resolve().parents[1]
    if not args.output.is_dir() or any(args.output.iterdir()):raise ValueError('Empty owned output required')
    manifest=json.loads((args.canonical/'manifest.json').read_text())
    if manifest.get('source_license_clearance')!='PASS':raise ValueError('Canonical source permission gate did not pass')
    assets={a['name']:a for a in manifest['assets']}
    inv=[a for a in manifest['assets'] if a['role']=='licenses']
    if len(inv)!=1 or sha256(args.canonical/filename(inv[0]['name']))!=inv[0]['sha256']:
        raise ValueError('Canonical source inventory differs')
    rows={}
    for line in args.packages.read_text().splitlines():
        name,*row=line.split('\t')
        if len(row)!=4 or row[1] not in {args.architecture,'all'} or name in rows:
            raise ValueError('Exact target package identities required')
        rows[name]=row
    fragment=select(json.loads((args.canonical/inv[0]['name']).read_text()),rows,args.parent,
                    json.loads(args.child_gaps.read_text()))
    groups=reference_groups(fragment);remap={};outputs=[]
    with tempfile.TemporaryDirectory() as temporary:
        work=Path(temporary)
        for index,(name,wanted) in enumerate(sorted(groups.items())):
            original=args.canonical/name;asset=assets.get(name,{})
            if (asset.get('role')!='source' or original.is_symlink() or not original.is_file()
                    or original.stat().st_size!=asset.get('size') or sha256(original)!=asset.get('sha256')):
                raise ValueError('Canonical preferred-source asset changed')
            selected=work/str(index);selected.mkdir();found=set();inputs=[]
            with tarfile.open(original) as source:
                for member in source:
                    if member.name not in wanted:continue
                    if not member.isfile() or member.name in found:raise ValueError('Ambiguous preferred-source member')
                    found.add(member.name);target=selected/member.name;target.parent.mkdir(parents=True,exist_ok=True)
                    with source.extractfile(member) as incoming,target.open('xb') as outgoing:
                        import shutil
                        shutil.copyfileobj(incoming,outgoing)
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
                    if sha256(target)!=wanted[member.name]['sha256']:raise ValueError('Preferred-source bytes differ')
                    inputs.append((target,member.name,wanted[member.name]['category']))
            if found!=wanted.keys():raise ValueError('Preferred-source member missing')
            receipt=selected/'export-receipt.json'
            receipt.write_text(json.dumps({'scope':'Preferred sources/notices only; no target binary or release clearance',
                'canonical_source_commit':manifest['source_commit'],'canonical_asset':name,
                'canonical_asset_sha256':asset['sha256'],'members':wanted},sort_keys=True)+'\n')
            inputs.extend([(Path(__file__).resolve(),'source-closure-export.py','sources'),
                           (repo/'LICENSE','source-closure-export-LICENSE','license_files'),
                           (receipt,'source-closure-export.json','build_inputs')])
            output=args.output/f"shirube-os-{manifest['version']}-{args.architecture}-source-supplement-{index:04d}.tar.gz"
            origin=None
            if any(m.startswith('netbird/') for m in wanted):
                origin='netbird:'+json.loads((repo/'source-lock.json').read_text())['netbird']['source_commit']
            elif any(m.startswith('vpp-preferred-inputs/') for m in wanted):
                origin='vpp:'+json.loads((repo/'source-lock.json').read_text())['external_git']['vpp']['commit']
            bundle(inputs,output,1773859634,source_origin=origin)
            for member in wanted:remap[(name,member)]={'asset':output.name,'member':member,'sha256':wanted[member]['sha256']}
            outputs.append({'name':output.name,'role':'source','architecture':args.architecture,
                            'size':output.stat().st_size,'sha256':sha256(output)})
            print('SOURCE_CLOSURE_EXPORTED: '+str(index+1)+'/'+str(len(groups)),flush=True)
    def rewrite(value):
        if isinstance(value,dict):
            if set(value)=={'asset','member','sha256'}:
                replacement=remap.get((value['asset'],value['member']))
                if replacement is not None:return replacement
            return {k:rewrite(v) for k,v in value.items()}
        if isinstance(value,list):return [rewrite(v) for v in value]
        return value
    result={'kind':'preferred-source-closure-supplement','canonical_source_commit':manifest['source_commit'],
            'target_architecture':args.architecture,'version':manifest['version'],'assets':outputs,
            'fragment':rewrite(fragment),'release_clearance':'NOT_CLEARED','binary_compilation':'NOT_PERFORMED'}
    (args.output/'source-closures.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
