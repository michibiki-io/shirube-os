"""Verify Go's documented h1 module ZIP identity without extracting its contents."""
import base64
import hashlib
from pathlib import PurePosixPath
import zipfile


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
