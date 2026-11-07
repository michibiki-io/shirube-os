"""Inspect actual Debian payloads, including ELF and module signature identity."""
import gzip
import lzma
from pathlib import Path
import subprocess
import tarfile
from .architecture import check_elf
from .release import SECRET
from .signing import verify_module


def audit(package, architecture, certificate=None):
    modules = 0
    with subprocess.Popen(['dpkg-deb', '--fsys-tarfile', str(package)], stdout=subprocess.PIPE) as process:
        try:
            with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
                for member in archive:
                    if not member.isfile():
                        continue
                    name = member.name.removeprefix('./')
                    with archive.extractfile(member) as source:
                        if name.endswith(('.ko', '.ko.gz', '.ko.xz', '.ko.zst')):
                            data = source.read()
                            if name.endswith('.gz'):
                                data = gzip.decompress(data)
                            elif name.endswith('.xz'):
                                data = lzma.decompress(data)
                            elif name.endswith('.zst'):
                                data = subprocess.check_output(['zstd', '-dc'], input=data)
                            check_elf(data, architecture)
                            if certificate is None:
                                raise ValueError('Cannot verify a module without the matching kernel certificate')
                            verify_module(data, certificate)
                            modules += 1
                        else:
                            first = source.read(1024**2)
                            if (first[:4] == b'\x7fELF' and '/firmware/' not in '/'+name
                                    and name.startswith(('usr/bin/', 'usr/sbin/', 'usr/lib/', 'usr/libexec/',
                                                         'bin/', 'sbin/', 'lib/', 'lib64/', 'opt/'))):
                                check_elf(first, architecture)
                            tail = b''
                            chunk = first
                            while chunk:
                                if SECRET.search(tail + chunk):
                                    raise ValueError('Private key in Debian package payload')
                                tail = chunk[-128:]
                                chunk = source.read(1024**2)
        finally:
            process.stdout.close()
        if process.wait() != 0:
            raise ValueError('Failed to inspect Debian payload')
    return {'elf': 'PASS', 'signed_modules_verified': modules}
