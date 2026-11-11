"""Verify Go's documented h1 module ZIP identity without extracting its contents."""
import base64
import hashlib
from pathlib import PurePosixPath
import zipfile
import re
import json
from pathlib import Path
import shutil


def build_info(text,require_sums=True):
    """Read actual linked dependencies, including Go module replacements."""
    lines=text.splitlines()
    if not lines or not re.search(r': go[0-9]+\.[0-9]+(?:\.[0-9]+)?$',lines[0]):
        raise ValueError('Missing Go compiler build information')
    result={'compiler':lines[0].rsplit(': go',1)[1],'modules':[],'settings':{}}
    for line in lines[1:]:
        fields=line.strip().split('\t')
        if fields[0] in {'dep','=>'}:
            if len(fields) not in {3,4}:raise ValueError('Invalid Go dependency metadata')
            module,version=fields[1:3]
            escape(module)
            if not re.fullmatch(r'v[A-Za-z0-9.+_-]+',version):
                raise ValueError('Unresolved or local Go dependency')
            record={'module':module,'version':version,'sum':fields[3] if len(fields)==4 else None}
            if fields[0]=='=>':
                if not result['modules']:raise ValueError('Unmatched Go dependency replacement')
                record['original_module']=result['modules'][-1]['module']
                result['modules'][-1]=record
            else:result['modules'].append(record)
        elif fields[0]=='mod' and len(fields)>=3:
            result['main']={'module':fields[1],'version':fields[2]}
        elif fields[0]=='build' and len(fields)==2:
            key,separator,value=fields[1].partition('=')
            if separator:result['settings'][key]=value
    identities=set()
    for record in result['modules']:
        identity=(record['module'],record['version'])
        if identity in identities:raise ValueError('Duplicate linked Go dependency')
        identities.add(identity)
        if require_sums and not re.fullmatch(r'h1:[A-Za-z0-9+/]{43}=',record['sum'] or ''):
            raise ValueError('Linked Go source checksum missing')
    return result


def module_hash(archive, module, version):
    prefix=module+'@'+version+'/'
    summary=hashlib.sha256();seen=set()
    with zipfile.ZipFile(archive) as source:
        for member in sorted(source.infolist(),key=lambda item:item.filename):
            name=member.filename;path=PurePosixPath(name)
            if (name in seen or not name.startswith(prefix) or path.is_absolute() or '..' in path.parts
                    or any(char in name for char in '\r\n\\') or member.file_size>512*1024**2):
                raise ValueError('Unsafe or duplicate Go module ZIP member')
            seen.add(name)
            with source.open(member) as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
            summary.update((digest+'  '+name+'\n').encode())
    return 'h1:'+base64.b64encode(summary.digest()).decode()


def escape(module):
    if PurePosixPath(module).is_absolute() or '..' in PurePosixPath(module).parts or '\\' in module:
        raise ValueError('Unsafe module path')
    return ''.join('!'+char.lower() if 'A'<=char<='Z' else char for char in module)


def retain_inputs(info,cache,destination):
    """Retain the full exact source ZIP and notices for every linked module."""
    cache=Path(cache).resolve();destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=False)
    inputs=destination/'inputs';inputs.mkdir()
    notices=destination/'notices';notices.mkdir()
    records=[]
    for dependency in info['modules']:
        module,version,expected=dependency['module'],dependency['version'],dependency['sum']
        if not expected:raise ValueError('Actual linked module checksum required')
        archive=cache/'cache/download'/escape(module)/'@v'/(escape(version)+'.zip')
        if archive.is_symlink() or not archive.is_file() or not archive.resolve().is_relative_to(cache):
            raise ValueError('Exact linked module ZIP missing from prepared cache')
        if module_hash(archive,module,version)!=expected:
            raise ValueError('Linked module source differs from actual binary build information')
        identity=hashlib.sha256((module+'@'+version).encode()).hexdigest()
        target=inputs/(identity+'.zip');shutil.copy2(archive,target)
        legal=[]
        with zipfile.ZipFile(archive) as source:
            for member in sorted(source.infolist(),key=lambda value:value.filename):
                path=PurePosixPath(member.filename);name=path.name.lower()
                wanted=(name.startswith(('license','licence','copying','copyright','notice','authors','patents'))
                        or any(part.lower() in {'licenses','licences'} for part in path.parts))
                if not member.is_dir() and wanted:
                    if member.file_size>4*1024**2:raise ValueError('Oversized upstream legal notice')
                    data=source.read(member)
                    if data.strip():legal.append((member.filename,data))
        if not legal:raise ValueError('Linked module has no retained legal notice')
        notice=notices/(identity+'.txt')
        with notice.open('wb') as stream:
            stream.write(('Component: '+module+'@'+version+'\n').encode())
            for member,data in legal:stream.write(('\nNotice: '+member+'\n').encode()+data+b'\n')
        with target.open('rb') as stream:source_sha256=hashlib.file_digest(stream,'sha256').hexdigest()
        records.append({**dependency,'source_identity':expected,
            'source':{'path':'inputs/'+target.name,'sha256':source_sha256},
            'license_notice':{'path':'notices/'+notice.name,'sha256':hashlib.sha256(notice.read_bytes()).hexdigest()}})
    (destination/'modules.json').write_text(json.dumps({'compiler':info['compiler'],'modules':records},indent=2)+'\n')
    return records
