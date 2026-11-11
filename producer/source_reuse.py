"""Bind preferred-source supplements to retained target build inputs.

Only source metadata is changed. Callers must prune unreferenced original
source members, verify all resulting archive bytes/permissions, and physically
restore the complete set before claiming a source check.
"""
import copy
import hashlib
import json


def unique(records):
    return list({(r['asset'],r['member'],r['sha256']):r for r in records}.values())


def upgrade_binding(old,new,sdk):
    if (old['name'],old['version'])!=(new['name'],new['version']):
        raise ValueError('Dependency version changed during source reuse')
    previous=old['source_identity'];current=new['source_identity']
    if previous==current or previous in {r['sha256'] for r in new['sources']}:return True
    exported=new.get('source_export',{})
    if previous in {exported.get('original_source_identity'),exported.get('original_archive_sha256')}:
        return True
    return any(r['name']==old['name'] and r['version']==old['version']
               and r['sha256']==previous and r.get('source_export',{}).get('sha256')==current
               for r in sdk['components'])


def apply(inventory,fragment,rows,lock,sdk):
    """Mutate an owned in-memory inventory; never mutate input archives."""
    parents={c['name']:c for c in inventory['components']};rewrites={};decisions=[]
    for name,source in fragment['parents'].items():
        row=rows[name]
        if [source['source_package'],source['source_version']]!=row[2:]:
            raise ValueError('Target source version differs from preferred source')
        old=parents.get(name)
        if old is not None:
            origin=source.get('source_origin',{}).get('debian_preferred_source_export',{})
            if (old['source_package']!=source['source_package'] or old['source_version']!=source['source_version']
                    or origin.get('original_descriptor_sha256')!=old['source_identity']):
                raise ValueError('Preferred Debian source does not bind the original descriptor')
        updated=copy.deepcopy(source)
        updated.update(version=row[0],architecture=row[1])
        if old is not None:updated['build_inputs']=unique(old['build_inputs']+updated['build_inputs'])
        parents[name]=updated
    replacements={(c['name'],c['version']):c for c in fragment['children']}
    def visit(parent):
        children=[]
        for old in parent.get('bundled_components',[]):
            source=replacements.get((old['name'],old['version']))
            if source is None:
                visit(old);children.append(old);continue
            if not upgrade_binding(old,source,sdk):
                raise ValueError('Preferred dependency source lacks an exact original-input binding: '+old['name'])
            child=copy.deepcopy(source)
            child['build_inputs']=unique(old['build_inputs']+child['build_inputs'])
            if 'binary_input' in old:child['binary_input']=old['binary_input']
            if old['source_identity']!=child['source_identity']:
                exported=child.get('source_export',{})
                original=exported.get('original_archive_sha256')
                if original is None:
                    declaration=next((r for r in sdk['components'] if r['name']==old['name']
                                      and r['version']==old['version'] and r['sha256']==old['source_identity']
                                      and r.get('source_export',{}).get('sha256')==child['source_identity']),None)
                    if declaration:original=declaration['sha256']
                if original:
                    if len(child['sources'])!=1:raise ValueError('Ambiguous preferred source replacement')
                    existing=rewrites.setdefault(original,child['sources'][0])
                    if existing!=child['sources'][0]:raise ValueError('Conflicting preferred source replacement')
                decisions.append({'name':old['name'],'version':old['version'],
                                  'original_source_identity':old['source_identity'],
                                  'preferred_source_identity':child['source_identity']})
            children.append(child)
        parent['bundled_components']=children
    for parent in parents.values():visit(parent)
    vpp=fragment['vpp'];wanted=vpp['source_origin']
    for parent in parents.values():
        if parent['source_package']!='vpp':continue
        original=parent['source_origin']
        if original!={k:v for k,v in wanted.items() if k!='vpp_retained_inputs'}:
            raise ValueError('VPP source revisions changed during source reuse')
        raw={r['member']:r for r in parent['sources'] if r['member'].startswith('source/build/external/downloads/')}
        children=[]
        for source in vpp['bundled_components']:
            child=copy.deepcopy(source);inputs=[]
            def retained(reference):
                if not reference['member'].startswith('source/build/external/downloads/'):return reference
                actual=raw.get(reference['member'])
                if actual is None or actual['sha256']!=reference['sha256']:
                    raise ValueError('Retained VPP input differs from preferred-source closure')
                return actual
            child['sources']=[retained(r) for r in child['sources']]
            for r in source['build_inputs']:
                if r['member'].startswith('source/build/external/downloads/'):inputs.append(retained(r))
            child['build_inputs']=unique(parent['build_inputs']+inputs)
            parent['sources']=unique(parent['sources']+child['sources'])
            parent['license_files']=unique(parent['license_files']+child['license_files'])
            children.append(child)
        parent.update(source_origin=copy.deepcopy(wanted),source_identity=vpp['source_identity'],bundled_components=children)
    integration=parents['shirube-integration'];origin=integration['source_origin'];netbird=lock['netbird']
    architecture=rows['shirube-integration'][1]
    if any(origin.get(k)!=v for k,v in {
        'netbird_source_commit':netbird['source_commit'],'netbird_source_sha256':netbird['source_sha256'],
        'headless_asset_sha256':netbird['assets'][architecture]['sha256']}.items()):
        raise ValueError('Retained NetBird binary/source binding differs from the public lock')
    origin.update(fragment['geoip']['origin'])
    integration['source_identity']=hashlib.sha256(json.dumps(origin,sort_keys=True).encode()).hexdigest()
    for source in fragment['geoip']['children']:
        if any(c['name']==source['name'] for c in integration['bundled_components']):
            raise ValueError('GeoIP source closure already exists')
        child=copy.deepcopy(source)
        child['build_inputs']=unique(integration['build_inputs']+child['build_inputs'])
        integration['bundled_components'].append(child)
        integration['sources']=unique(integration['sources']+child['sources'])
        integration['license_files']=unique(integration['license_files']+child['license_files'])
    # Original source-only material excluded by an explicit preferred-source
    # declaration must not survive as an extra member or a renamed build input.
    def rewrite(component):
        for category in ['sources','license_files','build_inputs']:
            component[category]=unique([rewrites.get(r['sha256'],r) for r in component[category]])
        for child in component.get('bundled_components',[]):rewrite(child)
    for parent in parents.values():rewrite(parent)
    actual={(c['name'],c['version'],c['architecture'],c['source_package'],c['source_version']) for c in parents.values()}
    expected={(name,*row) for name,row in rows.items()}
    if actual!=expected:raise ValueError('Reused source inventory differs from actual target packages')
    inventory.update(components=list(parents.values()),shipped_packages=[
        {'name':name,'version':row[0],'architecture':row[1]} for name,row in rows.items()])
    return decisions
