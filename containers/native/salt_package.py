#!/usr/bin/python3
"""Apply the declared downstream Salt runtime profile to a complete package set."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import tempfile
sys.path.insert(0,'/src')
from producer.packages import audit
from salt_profile import runtime

VERSION='3006.21+shirube1'


def process(directory,architecture):
    packages=sorted(Path(directory).glob('salt*_3006.21_'+architecture+'.deb'))
    if len(packages)!=8:raise ValueError('Complete eight-package Salt set required')
    outputs=[]
    for package in packages:
        name=subprocess.check_output(['dpkg-deb','-f',str(package),'Package'],text=True).strip()
        with tempfile.TemporaryDirectory(prefix='shirube-salt-profile-') as temporary:
            root=Path(temporary)/'package'
            subprocess.run(['dpkg-deb','-R',str(package),str(root)],check=True)
            control=root/'DEBIAN/control';text=control.read_text()
            if re.findall(r'^Version: (.+)$',text,re.M)!=['3006.21']:raise ValueError('Unexpected Salt package version')
            text=re.sub(r'^Version: .+$','Version: '+VERSION,text,flags=re.M)
            text=re.sub(r'^Source: salt(?: \([^)]*\))?$','Source: salt ('+VERSION+')',text,flags=re.M)
            text=re.sub(r'(salt-[a-z-]+\s*\(=\s*)3006\.21(\))',lambda match:match[1]+VERSION+match[2],text)
            control.write_text(text)
            if name=='salt-common':
                runtime(root/'opt/saltstack/salt')
                notice=root/'usr/share/doc/salt-common/copyright'
                if not notice.is_file():raise ValueError('Salt original copyright notice missing')
                with notice.open('a') as output:
                    output.write('\nShirube OS packaging modifications, michibiki-io, 2026-11-08:\n'
                        'Unused Cryptodome SelfTest runtime files excluded; incomplete private-key documentation examples redacted.\n'
                        'Upstream license terms and corresponding preferred sources remain applicable.\n')
            sums=[]
            for path in sorted(root.rglob('*')):
                if path.is_file() and not path.is_symlink() and not path.is_relative_to(root/'DEBIAN'):
                    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'md5').hexdigest()
                    sums.append(digest+'  '+str(path.relative_to(root))+'\n')
            (root/'DEBIAN/md5sums').write_text(''.join(sums))
            target=package.with_name(name+'_'+VERSION+'_'+architecture+'.deb')
            subprocess.run(['dpkg-deb','--root-owner-group','-b',str(root),str(target)],check=True)
            audit(target,architecture);outputs.append((package,target))
    # Never leave an unsafe older package in the candidate's pool.
    for original,_ in outputs:original.unlink()
    return [target for _,target in outputs]


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);parser.add_argument('architecture',choices=['amd64','arm64'])
    args=parser.parse_args();process(args.directory,args.architecture)
