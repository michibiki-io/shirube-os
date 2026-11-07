#!/usr/bin/python3
"""Install the complete pinned FPM gem set without dependency resolution."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request


def install(lock):
    records=json.loads(Path(lock).read_text())['gems']
    seen=set()
    with tempfile.TemporaryDirectory() as temporary:
        paths=[]
        for record in records:
            name,version=record['name'],record['version']
            if (not re.fullmatch(r'[a-z0-9][a-z0-9_-]*',name) or name in seen
                    or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)+',version)
                    or not re.fullmatch(r'[0-9a-f]{64}',record['sha256'])):
                raise ValueError('Invalid or duplicate locked gem')
            seen.add(name)
            filename=name+'-'+version+'.gem'
            url='https://rubygems.org/downloads/'+filename
            if record['url'] != url:
                raise ValueError('Unexpected gem source')
            path=Path(temporary)/filename
            with urllib.request.urlopen(url,timeout=60) as response:
                if not response.url.startswith('https://'):
                    raise ValueError('Insecure gem redirect')
                data=response.read(16*1024**2+1)
            if len(data)>16*1024**2 or hashlib.sha256(data).hexdigest()!=record['sha256']:
                raise ValueError('Locked gem checksum mismatch')
            path.write_bytes(data);paths.append(path)
        if 'fpm' not in seen:
            raise ValueError('FPM must be present in the complete gem lock')
        subprocess.run(['gem','install','--local','--ignore-dependencies','--no-document',
                        *map(str,paths)],check=True)
        actual=subprocess.check_output(['fpm','--version'],text=True).strip()
        expected=next(x['version'] for x in records if x['name']=='fpm')
        if actual!=expected:
            raise ValueError('Installed FPM version mismatch')


if __name__=='__main__':
    install(sys.argv[1])
