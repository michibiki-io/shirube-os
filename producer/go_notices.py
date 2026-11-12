"""Generic Go compiler and standard-library legal notice retention."""
from pathlib import Path
import tarfile


def go_notice(archive,target):
    count=0
    with tarfile.open(archive) as source,target.open('wb') as output:
        output.write(b'Go compiler/standard-library source notices; upstream terms remain applicable.\n')
        for member in source:
            path=Path(member.name)
            if (member.isfile() and (path.name.lower().startswith(('license','licence','copying','copyright','notice','patents','authors'))
                    or any(part.lower() in {'licenses','licences'} for part in path.parts))):
                if member.size>4*1024**2:raise ValueError('Oversized Go legal notice')
                data=source.extractfile(member).read()
                if data.strip():output.write(('\nNotice: '+member.name+'\n').encode()+data+b'\n');count+=1
    if not count:raise ValueError('Go source has no retained legal notices')
