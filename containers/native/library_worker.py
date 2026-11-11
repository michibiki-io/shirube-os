"""Compile one unsigned user-space library and export its exact public inputs."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
from resources import environment,profile


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def audit(package,arch):
    # This stage must never contain a kernel or a signing/enrollment operation.
    secret=re.compile(rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----[ \t]*(?:\r?\n|\\n)(?:(?:Proc-Type|DEK-Info):[^\r\n]{1,256}\r?\n)*(?:\r?\n)*[A-Za-z0-9/+]{32}')
    with subprocess.Popen(['dpkg-deb','--fsys-tarfile',str(package)],stdout=subprocess.PIPE) as process:
        try:
            with tarfile.open(fileobj=process.stdout,mode='r|') as archive:
                for member in archive:
                    if not member.isfile():continue
                    if member.name.endswith(('.ko','.ko.xz','.ko.gz','.ko.zst')):
                        raise ValueError('Kernel modules cannot enter a library cache')
                    with archive.extractfile(member) as source:
                        first=source.read(1024**2)
                        if first.startswith(b'\x7fELF') and (first[4:6]!=b'\x02\x01' or int.from_bytes(first[18:20],'little')!={'amd64':62,'arm64':183}[arch]):
                            raise ValueError('Foreign ELF in library cache')
                        tail=b'';chunk=first
                        while chunk:
                            if secret.search(tail+chunk):raise ValueError('Private key in library cache package')
                            tail=(tail+chunk)[-1024:];chunk=source.read(1024**2)
        finally:process.stdout.close()
        if process.wait():raise ValueError('Library package inspection failed')


def main():
    context=json.loads(Path('/context.json').read_text())
    arch=subprocess.check_output(['dpkg','--print-architecture'],text=True).strip()
    if arch!=context['architecture']:raise ValueError('Native library architecture mismatch')
    export=Path('/export');export.mkdir();Path('/output').mkdir()
    # Retain the preferred original source and its Debian patches, before any
    # compiler/test can generate private state. All acquisition was authenticated.
    with tarfile.open(export/'source.tar.gz','w:gz') as archive:
        archive.add('/snapshot',arcname='source',filter=lambda item:None if '.git' in Path(item.name).parts else item)
    shutil.copy2('/common.sh',export/'common.sh');shutil.copy2('/prepare.sh',export/'prepare.sh')
    shutil.copy2('/context.json',export/'context.json')
    shutil.copytree('/usr/share/common-licenses',export/'common-licenses')
    env=environment();env['SOURCE_DATE_EPOCH']='1773859634'
    subprocess.run(['bash','/common.sh'],check=True,env=env)
    packages=[]
    for package in sorted(Path('/output').glob('*.deb')):
        if '-build-deps_' in package.name:continue
        actual=subprocess.check_output(['dpkg-deb','-f',str(package),'Architecture'],text=True).strip()
        if actual not in {arch,'all'}:raise ValueError('Foreign library package architecture')
        audit(package,arch);target=export/package.name;shutil.copy2(package,target)
        packages.append({'name':package.name,'sha256':digest(target),'architecture':actual})
    if not packages:raise ValueError('Empty library cache output')
    files=[{'name':str(path.relative_to(export)),'sha256':digest(path)} for path in sorted(export.rglob('*')) if path.is_file()]
    (export/'receipt.json').write_text(json.dumps({'schema_version':1,'input_id':context['input_id'],
        'architecture':arch,'job':context['job'],'resources':profile(),'packages':packages,'files':files},indent=2)+'\n')
    # Export never includes compiled workspaces, generated test state or keys.
    shutil.rmtree('/output')


if __name__=='__main__':main()
