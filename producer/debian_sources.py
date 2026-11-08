"""Restore authenticated Debian inputs and retain original source legal files."""
from email.parser import Parser
from pathlib import Path
import re
import subprocess
from .sources import sha256


def descriptor(directory, source, version):
    directory=Path(directory).resolve()
    candidates=list(directory.glob('*.dsc'))
    if len(candidates)!=1 or candidates[0].is_symlink():
        raise ValueError('Exactly one regular Debian source descriptor is required')
    dsc=candidates[0]
    text=dsc.read_text()
    if text.startswith('-----BEGIN PGP SIGNED MESSAGE-----'):
        text=text.split('\n\n',1)[1]
    fields=Parser().parsestr(text.split('-----BEGIN PGP SIGNATURE-----',1)[0])
    for field in ['Source','Version','Checksums-Sha256']:
        if len(fields.get_all(field,[]))!=1:
            raise ValueError('Missing or duplicate Debian source identity/checksums')
    if fields['Source']!=source or fields['Version']!=version:
        raise ValueError('Debian source identity differs from the installed package')
    seen=set()
    for line in fields['Checksums-Sha256'].splitlines():
        if not line.strip():continue
        values=line.split()
        if len(values)!=3:raise ValueError('Invalid Debian source checksum row')
        digest,size,name=values
        if (not re.fullmatch('[0-9a-f]{64}',digest) or not size.isdecimal() or
                not re.fullmatch('[A-Za-z0-9][A-Za-z0-9.+_%~=-]*',name) or name in seen):
            raise ValueError('Unsafe or duplicate Debian source archive')
        seen.add(name);path=directory/name
        if (path.is_symlink() or not path.is_file() or path.stat().st_size!=int(size)
                or sha256(path)!=digest):
            raise ValueError('Debian source archive is missing or differs')
    if not seen:raise ValueError('Empty Debian corresponding source set')
    return dsc


def unpack(directory, source, version, destination):
    """APT authenticates the source index; retain/recheck all descriptor hashes.

    This is source-tree reconstruction, not a binary rebuild. No additional
    signature claim is made by dpkg-source's extraction-only invocation.
    """
    dsc=descriptor(directory,source,version)
    destination=Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError('A fresh Debian source reconstruction directory is required')
    subprocess.run(['dpkg-source','--no-check','-x',str(dsc),str(destination)],check=True,
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return dsc


def legal_files(root):
    root=Path(root).resolve()
    for path in sorted(root.rglob('*')):
        relative=path.relative_to(root)
        if any(part in {'.git','.pc','__pycache__'} for part in relative.parts):continue
        name=path.name.lower()
        legal=(any(part.lower()=='licenses' for part in relative.parts[:-1]) or
               bool(re.fullmatch(r'(?:copying|copyrights?|licen[cs]es?|licensing|notices?)(?:[0-9._-].*)?',name)))
        if not legal or path.suffix.lower() in {'.py','.c','.h','.cc','.cpp','.js'}:continue
        if path.is_symlink() and path.is_dir():
            raise ValueError('Source legal directory alias requires explicit materialization')
        if not path.resolve().is_relative_to(root):
            raise ValueError('Original source legal file points outside its source tree')
        if not path.is_file():continue
        yield path,str(relative)
